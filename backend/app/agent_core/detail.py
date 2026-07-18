from __future__ import annotations
import logging
from typing import Any, Dict, List, Tuple
from app.schemas import AdviceResult, FactCard
from app.nlu.preprocess import strip_accents
from app.agent_core.presenters import product_display_name, build_detail_card
from app.advice.provenance import facts_for_llm
from app.advice.verify import verify_advice, is_grounded

log = logging.getLogger("agent_core")

_POSITION: Dict[str, int] = {
    "dau tien": 0, "thu nhat": 0, "may 1": 0, "cai 1": 0, "so 1": 0, "thu 1": 0, "mau 1": 0,
    "thu hai": 1, "may 2": 1, "cai 2": 1, "so 2": 1, "thu 2": 1, "mau 2": 1, "o giua": 1,
    "cuoi cung": 2, "thu ba": 2, "may 3": 2, "cai 3": 2, "so 3": 2, "thu 3": 2, "mau 3": 2, "cuoi": 2,
}
_DETAIL_KW = ["chi tiet", "ky hon", "ky ve", "cu the", "thong so", "bao nhieu", "the nao",
              "co gi", "noi them", "noi ro", "bao hanh", "kich thuoc", "can nang", "khoi luong",
              "mau sac", "cong nghe", "tinh nang", "dung tich", "pin", "man hinh", "chi so",
              "co tot khong", "danh gia", "tim hieu", "xem them", "ra sao", "nhu the nao",
              "kieu dang", "xuat xu", "san xuat", "cong suat", "trong luong",
              "diem manh", "uu diem", "nhuoc diem"]
_LIST_KW = ["may khac", "san pham khac", "cai khac", "lua chon khac", "danh sach",
            "may nao khac", "con gi khac", "quay lai", "xem lai danh sach", "so sanh lai"]

_DETAIL_SYSTEM = (
    "Bạn là nhân viên tư vấn điện máy thân thiện, nói tiếng Việt bình dân. Khách đang hỏi kỹ về MỘT "
    "sản phẩm. Bạn CHỈ được dùng dữ kiện trong phần FACTS; TUYỆT ĐỐI không bịa thông số, giá, khuyến mãi, "
    "tồn kho, đánh giá. Nếu thông tin khách hỏi không có trong FACTS, nói thẳng 'dạ em chưa có dữ liệu về ... ạ'. "
    "Trả lời thẳng vào câu hỏi, ngắn gọn, thân thiện."
)


def is_detail_question(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(kw in flat for kw in _DETAIL_KW)


def wants_product_list(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(k in flat for k in _LIST_KW)


def resolve_product_row(message: str, rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not rows:
        return None
    flat = strip_accents(message.lower())
    for key, idx in _POSITION.items():
        if key in flat and idx < len(rows):
            return rows[idx]
    for r in rows:
        b = strip_accents((r.get("brand") or "").lower()).strip()
        if len(b) >= 2 and b in flat:
            return r
    priced = [r for r in rows if float(r.get("price_clean") or 0) > 0]
    if priced and ("re nhat" in flat or "gia thap nhat" in flat or "gia tot nhat" in flat):
        return min(priced, key=lambda r: float(r["price_clean"]))
    if priced and ("dat nhat" in flat or "cao cap nhat" in flat or "xin nhat" in flat):
        return max(priced, key=lambda r: float(r["price_clean"]))
    return None


def _safe_summary(row: Dict[str, Any], card: FactCard) -> str:
    keep = [l for l in card.lines if l.label in ("Giá", "Thương hiệu")]
    head = "; ".join(f"{l.label} {l.value}" for l in keep) if keep else "thông tin cơ bản"
    return (f"Dạ về {product_display_name(row)}: {head}. "
            "Anh/chị muốn biết thêm thông số cụ thể nào ạ?")


def answer_detail(row: Dict[str, Any], question: str, llm) -> Tuple[str, FactCard]:
    """Trả lời sâu 1 sản phẩm, grounded trong fact-sheet; fail-closed nếu LLM bịa số."""
    card = build_detail_card(row)
    facts = facts_for_llm([card])
    user = (f'Khách hỏi về "{product_display_name(row)}": "{question}"\n\n'
            f"FACTS:\n{facts}\n\nTrả lời khách theo đúng quy tắc.")
    try:
        message = llm.complete_text(_DETAIL_SYSTEM, user)
    except Exception as e:
        log.warning("detail: LLM lỗi (%s) -> safe summary", e)
        message = ""
    result = verify_advice(AdviceResult(message=message or "", cards=[card], assumptions=[], warnings=[]))
    if not message or not is_grounded(result):
        if message:
            log.warning("detail: FAIL-CLOSED — số không truy được nguồn -> safe summary (warnings=%s)",
                        list(result.warnings))
        return _safe_summary(row, card), card
    log.info("detail: câu trả lời LLM grounded")
    return message, card
