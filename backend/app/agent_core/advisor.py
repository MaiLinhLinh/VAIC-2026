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
    "5. Giọng chuyên nghiệp, mạch lạc, súc tích, đúng ngữ pháp."
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
    if status == "no_products_found":
        cat = f" thuộc danh mục **{intent['category']}**" if intent.get("category") else ""
        bud = (f" trong mức dưới **{format_vnd(int(intent['budget_max']))}**"
               if intent.get("budget_max") else "")
        return (f"Rất tiếc, hiện chưa có sản phẩm nào{cat}{bud} khớp yêu cầu của bạn.\n\n"
                "Bạn thử nới ngân sách, đổi thương hiệu hoặc danh mục khác nhé!")
    return None


def generate_advisor(query: str, intent: Dict[str, Any], rows: List[Dict[str, Any]],
                     status: str, llm, cards: List[FactCard],
                     on_delta: Optional[Callable[[str], None]] = None) -> Tuple[str, bool, List[str]]:
    """Sinh tư vấn top-3 + trade-off. Trả (message, streamed, warnings). Fail-closed nếu bịa số."""
    det = deterministic_message(intent, status, None)
    if det is not None:
        log.info("advisor: dùng văn mẫu tất định (status=%s), không gọi LLM", status)
        return det, False, []
    if not rows:
        return ("Rất tiếc, hiện chưa có sản phẩm phù hợp. Bạn thử nới ngân sách hoặc đổi tiêu chí nhé!",
                False, [])

    facts = facts_for_llm(cards)
    assump = [a for a in (intent.get("assumptions") or []) if a]
    assump_txt = (f"Giả định của em (phải nói rõ với khách rằng đây là em đang giả định): "
                  f"{'; '.join(assump)}\n" if assump else "")
    user = (f"Trạng thái tìm kiếm: {status}\nFACTS (chỉ dùng dữ kiện này):\n{facts}\n\n"
            f"Nhu cầu khách: {query}\n{assump_txt}\nHãy tư vấn top sản phẩm kèm phân tích trade-off.")

    # Streaming: phát từng dòng đã verify (line-level fail-closed).
    if on_delta is not None:
        allowed = allowed_numbers(cards)
        parts: List[str] = []
        buf = ""
        emitting = True

        def push(line: str) -> None:
            nonlocal emitting
            if emitting and line_is_grounded(line, allowed):
                on_delta(line)
            else:
                emitting = False

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
            log.warning("advisor(stream): FAIL-CLOSED — số không truy được nguồn -> safe summary "
                        "(warnings=%s)", list(result.warnings))
            return _safe_summary(cards), False, list(result.warnings)
        log.info("advisor(stream): câu trả lời LLM grounded, phát đủ")
        return result.message, emitting, []

    return _blocking(llm, user, cards)


def _blocking(llm, user: str, cards: List[FactCard]) -> Tuple[str, bool, List[str]]:
    try:
        message = llm.complete_text(_SYSTEM, user)
    except Exception as e:
        log.warning("advisor: LLM lỗi (%s) -> safe summary", e)
        return _safe_summary(cards), False, []
    result = verify_advice(AdviceResult(message=message, cards=cards, assumptions=[], warnings=[]))
    if not is_grounded(result):
        log.warning("advisor: FAIL-CLOSED — số không truy được nguồn -> safe summary (warnings=%s)",
                    list(result.warnings))
        return _safe_summary(cards), False, list(result.warnings)
    log.info("advisor: câu trả lời LLM grounded")
    return result.message, False, []


def _safe_summary(cards: List[FactCard]) -> str:
    lines = ["Dạ em gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        title = c.title.replace("Vì sao em đề xuất ", "").rstrip("?")
        lines.append(f"{i}. {title} — giá {price}.")
    return "\n".join(lines)
