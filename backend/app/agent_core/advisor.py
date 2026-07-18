from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
from app.schemas import AdviceResult, FactCard
from app.agent_core.presenters import build_reco_card
from app.agent_core.retriever import get_catalog_metadata
from app.advice.provenance import facts_for_llm, format_vnd
from app.advice.verify import allowed_numbers, line_is_grounded, verify_advice, is_grounded

log = logging.getLogger("agent_core")

_SYSTEM = (
    "Bạn là chuyên gia tư vấn điện máy. NGUYÊN TẮC:\n"
    "1. CHỈ dùng đúng các con số xuất hiện trong FACTS (giá, thông số). TUYỆT ĐỐI không nêu bất kỳ "
    "con số nào KHÔNG có trong FACTS — không ước lượng tiền điện/tháng, không tự tính mức tiết kiệm, "
    "không đưa phần trăm/kWh/nghìn đồng suy diễn. Nếu muốn nói về lợi ích, hãy mô tả BẰNG LỜI, không kèm số.\n"
    "1b. Viết MỌI con số Y HỆT định dạng trong FACTS (ví dụ giá '10.010.000đ' phải giữ nguyên, "
    "KHÔNG đổi sang '10 triệu' hay '10,01 triệu'; thông số giữ nguyên đơn vị như FACTS).\n"
    "1c. KHÔNG cộng/gộp/tính tổng hay làm tròn các con số (ví dụ KHÔNG cộng dung tích ngăn đá + ngăn "
    "lạnh thành 'tổng ~330 lít'); chỉ nêu lại từng con số đúng như FACTS.\n"
    "2. Trình bày thông số bằng lợi ích thực tế (VD: Inverter -> tiết kiệm điện, RAM lớn -> đa nhiệm mượt, ...) "
    "nhưng không gắn con số tự bịa.\n"
    "3. Giao diện (UI) đã TỰ ĐỘNG hiển thị các thẻ thông tin sản phẩm (Facts Card) kèm đầy đủ thông số, giá, khuyến mãi ở bên dưới tin nhắn của bạn. VÌ VẬY, TUYỆT ĐỐI KHÔNG LIỆT KÊ lại toàn bộ thông số, không viết format dạng danh sách dài dòng. CHỈ giới thiệu tên mẫu và 1-2 điểm đặc biệt nhất (VD: tính năng vượt trội nhất hoặc lý do nó phù hợp).\n"
    "4. Nếu trạng thái là budget_fallback: nói rõ không có sản phẩm trong ngân sách đó, rồi giới thiệu "
    "các mẫu giá gần nhất và ưu điểm để khách cân nhắc tăng ngân sách.\n"
    "4b. Nếu trạng thái là price_spread: khách nhờ chọn giúp và chưa chốt ngân sách — nói rõ em chọn "
    "đại diện 3 tầm giá (tiết kiệm / tầm trung / cao cấp) để anh/chị dễ định hình, rồi giới thiệu ngắn gọn từng mức.\n"
    "4c. Nếu kết quả trả về KHÔNG KHỚP HOÀN TOÀN với yêu cầu của khách (ví dụ: vượt ngân sách, khác thương hiệu, thiếu tính năng), BẮT BUỘC phải nói rõ sự sai lệch này.\n"
    "4d. Nếu không tìm thấy sản phẩm nào (FACTS trống rỗng), hãy lịch sự xin lỗi khách và gợi mở nhu cầu khác. TUYỆT ĐỐI KHÔNG đề xuất máy móc khi FACTS trống.\n"
    "5. Giọng điệu tự nhiên, ngắn gọn như người thật đang chat (tối đa 3-4 câu). KHÔNG dùng định dạng Markdown dài dòng (không dùng `#`, `*`, không chia mục Header lớn).\n"
)


def build_cards(rows: List[Dict[str, Any]], priority_features: List[str]) -> List[FactCard]:
    return [build_reco_card(r, priority_features) for r in rows]


def deterministic_message(intent: Dict[str, Any], status: str, db_path: Optional[str] = None) -> Optional[str]:
    """Copy tất định cho meta_inquiry / no_products_found; None nếu cần LLM."""
    if status == "meta_inquiry":
        meta = get_catalog_metadata(db_path)
        cats = ", ".join(f"**{c}**" for c in meta["categories"])
        return (f"Chào bạn, hệ thống hiện có **{len(meta['categories'])} danh mục** chính:\n\n{cats}\n\n"
                "Bạn quan tâm danh mục nào, ngân sách và tính năng ra sao ạ?")
    return None


def generate_advisor(query: str, intent: Dict[str, Any], rows: List[Dict[str, Any]],
                     status: str, llm, cards: List[FactCard],
                     on_delta: Optional[Callable[[str], None]] = None) -> Tuple[str, bool, List[str]]:
    """Sinh tư vấn top-3 + trade-off. Trả (message, streamed, warnings). Fail-closed nếu bịa số."""
    det = deterministic_message(intent, status, None)
    if det is not None:
        log.info("advisor: dùng văn mẫu tất định (status=%s), không gọi LLM", status)
        return det, False, []

    facts = facts_for_llm(cards)
    assump = [a for a in (intent.get("assumptions") or []) if a]
    assump_txt = (f"Giả định của em (phải nói rõ với khách rằng đây là em đang giả định): "
                  f"{'; '.join(assump)}\n" if assump else "")
    transition = intent.get("transition_message")
    trans_txt = f"LỜI CHUYỂN TIẾP (BẮT BUỘC dùng câu này làm câu mở đầu để giải thích sự suy luận): {transition}\n" if transition else ""
    
    wants_comp = intent.get("wants_comparison", False)
    if not wants_comp and len(cards) > 0:
        action = "Hãy CHỈ TRẢ LỜI NGẮN GỌN (2-3 câu), nêu tên sản phẩm và 1 điểm ăn tiền nhất. KHÔNG phân tích ưu nhược điểm. KẾT THÚC bằng câu hỏi: 'Anh/chị có muốn em phân tích so sánh chi tiết ưu nhược điểm (trade-off) không ạ?'."
    else:
        action = "Khách đã yêu cầu so sánh. Hãy phân tích đánh đổi (trade-off) ngắn gọn giữa các lựa chọn để khách dễ ra quyết định."
        
    user = (f"Trạng thái tìm kiếm: {status}\nFACTS (chỉ dùng dữ kiện này, nhưng đừng liệt kê lại vì UI đã hiển thị):\n{facts}\n\n"
            f"Nhu cầu khách: {query}\n{assump_txt}{trans_txt}\nCHỈ THỊ: {action}")

    # Streaming: phát từng dòng đã verify (line-level fail-closed).
    if on_delta is not None:
        allowed = allowed_numbers(cards)
        parts: List[str] = []
        buf = ""
        emitting = True

        def push(line: str) -> None:
            if line:
                on_delta(line)

        try:
            for token in llm.stream_text(_SYSTEM, user):
                parts.append(token)
                buf += token
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    push(line + "\n")
            if buf:
                push(buf)
        except Exception:
            return _blocking(llm, user, cards)
        result = verify_advice(AdviceResult(message="".join(parts), cards=cards, assumptions=[], warnings=[]))
        if not is_grounded(result):
            log.warning("advisor(stream): LLM vi phạm lỗi số liệu, cảnh báo (warnings=%s)", list(result.warnings))
            return result.message, True, list(result.warnings)
        log.info("advisor(stream): câu trả lời LLM grounded, phát đủ")
        return result.message, True, []

    return _blocking(llm, user, cards)


def _blocking(llm, user: str, cards: List[FactCard]) -> Tuple[str, bool, List[str]]:
    try:
        message = llm.complete_text(_SYSTEM, user)
    except Exception as e:
        log.warning("advisor: LLM lỗi (%s) -> safe summary", e)
        return _safe_summary(cards), False, []
    result = verify_advice(AdviceResult(message=message, cards=cards, assumptions=[], warnings=[]))
    if not is_grounded(result):
        log.warning("advisor: LLM vi phạm lỗi số liệu, cảnh báo (warnings=%s)", list(result.warnings))
        return result.message, False, list(result.warnings)
    log.info("advisor: câu trả lời LLM grounded")
    return result.message, False, []


def _safe_summary(cards: List[FactCard]) -> str:
    lines = ["Dạ em gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        title = c.title.replace("Vì sao em đề xuất ", "").rstrip("?")
        url = next((l.value for l in c.lines if l.label == "Link sản phẩm"), "")
        url_text = f" - Xem chi tiết: {url}" if url else ""
        lines.append(f"{i}. {title} — giá {price}.{url_text}")
    return "\n".join(lines)
