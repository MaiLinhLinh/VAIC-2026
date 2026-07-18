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
    "3. Phân tích đánh đổi (trade-off) rõ giữa các lựa chọn để khách dễ quyết.\n"
    "4. Nếu trạng thái là budget_fallback: nói rõ không có sản phẩm trong ngân sách đó, rồi giới thiệu "
    "các mẫu giá gần nhất và ưu điểm để khách cân nhắc tăng ngân sách.\n"
    "4b. Nếu trạng thái là price_spread: khách nhờ chọn giúp và chưa chốt ngân sách — nói rõ em chọn "
    "đại diện 3 tầm giá (tiết kiệm / tầm trung / cao cấp) để anh/chị dễ định hình, rồi giới thiệu từng mức.\n"
    "4c. Nếu trạng thái là custom_query: danh sách đã lọc đúng theo ràng buộc thông số khách nêu — "
    "nêu bật thông số đáp ứng ràng buộc đó (chỉ dùng số trong FACTS).\n"
    "4d. Nếu kết quả trả về KHÔNG KHỚP HOÀN TOÀN với yêu cầu của khách (ví dụ: vượt ngân sách, khác thương hiệu, thiếu tính năng), BẮT BUỘC phải nói rõ sự sai lệch này (VD: 'Mẫu này vượt ngân sách một chút', 'Mẫu này không hỗ trợ tính năng X'). TUYỆT ĐỐI không tự bịa tính năng để ép cho khớp.\n"
    "4e. ĐẶC BIỆT NHẤN MẠNH vào các tính năng mà khách đã yêu cầu (VD: khách cần 'nghe gọi', phải chỉ rõ mẫu nào có khả năng nghe gọi, mẫu nào không dựa vào phần FACTS).\n"
    "4f. Nếu không tìm thấy sản phẩm nào (FACTS trống rỗng), hãy lịch sự xin lỗi khách, giải thích lý do (dựa trên yêu cầu của khách không có trong dữ liệu) và đóng vai một người sale chuyên nghiệp để hỏi gợi mở sang một nhu cầu/tiêu chí khác. TUYỆT ĐỐI KHÔNG đề xuất máy móc khi FACTS trống.\n"
    "5. Giọng chuyên nghiệp, mạch lạc, súc tích, đúng ngữ pháp.\n"
    "6. KHÔNG dùng định dạng Markdown (tuyệt đối không dùng dấu sao `*` hoặc `#` để in đậm/in nghiêng). Để tạo danh sách, hãy xuống dòng và dùng dấu `-` hoặc số `1.` bình thường."
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
        action = "Hãy ĐỀ XUẤT NGẮN GỌN 1-2 sản phẩm phù hợp nhất (chỉ nêu 2-3 điểm nổi bật nhất, tuyệt đối không liệt kê toàn bộ thông số dài dòng). Cuối câu, hãy hỏi khách xem họ có muốn xem thêm các lựa chọn khác hoặc xem bảng so sánh không."
    else:
        action = "Hãy tư vấn các sản phẩm kèm phân tích đánh đổi (trade-off) chi tiết giữa các lựa chọn."
        
    user = (f"Trạng thái tìm kiếm: {status}\nFACTS (chỉ dùng dữ kiện này):\n{facts}\n\n"
            f"Nhu cầu khách: {query}\n{assump_txt}{trans_txt}{action}")

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
