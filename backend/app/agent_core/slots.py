"""Slot theo ngành hàng — thu thập nhu cầu dạng lấp dần các thông số ĐẶC THÙ của ngành.

Slot lấy trực tiếp từ CỘT thông số của bảng ngành trong DB (không hardcode). Mỗi slot có
trạng thái: 'filled' (có thông tin — khách nói hoặc AI giả định từ chân dung), 'dontcare'
(khách nói không biết/không quan trọng), 'unasked' (chưa đề cập). AI theo dõi + cập nhật
slot mỗi lượt và soạn câu hỏi tiếp theo (hỏi thẳng thông số hoặc hỏi chân dung để suy slot).
"""
from __future__ import annotations
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from app.config import get_settings

log = logging.getLogger("agent_core")

# Ngưỡng chuyển sang bước xác nhận so sánh (tính theo từng ngành, không phải toàn phiên).
FILLED_THRESHOLD = 3   # >= 3 slot CÓ THÔNG TIN thật
TOUCHED_THRESHOLD = 6  # hoặc >= 6 slot đã xác định (gồm cả 'không biết')

# Cột không phải thông số quyết định mua -> không dùng làm slot.
_NON_SPEC = {
    "model_code", "sku", "productidweb", "category_code", "brand_id", "brand",
    "giá gốc", "giá khuyến mãi", "khuyến mãi quà", "price_promo_clean", "capacity_clean",
    "nguồn giá", "giá hiệu lực",
}
_NON_SPEC_SUFFIX = ("(nguyên văn)", "(crawl)")

_SLOT_HINT = ('{"slots": [{"name": string, "value": string|null, '
              '"status": "filled"|"dontcare"|"unasked", '
              '"basis": "stated"|"interpreted"}], "next_question": string|null}')


def _resolve_db(db_path: Optional[str]) -> str:
    return db_path or get_settings().agent_db_path


def spec_slot_columns(category_table: str, db_path: Optional[str] = None) -> List[str]:
    """Các cột thông số dùng làm slot cho một ngành (đã loại cột kỹ thuật/giá/crawl)."""
    if not category_table:
        return []
    conn = sqlite3.connect(_resolve_db(db_path))
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{category_table}")')]
    finally:
        conn.close()
    out = []
    for c in cols:
        if c in _NON_SPEC or any(c.endswith(s) for s in _NON_SPEC_SUFFIX):
            continue
        out.append(c)
    return out


def count_filled(slots: List[Dict[str, Any]]) -> int:
    # Chỉ tính slot có thông tin THẬT từ khách (nói rõ / diễn giải trực tiếp) — không tính đồ đoán.
    return sum(1 for s in slots
               if s.get("status") == "filled" and s.get("basis") in ("stated", "interpreted"))


def count_touched(slots: List[Dict[str, Any]]) -> int:
    return sum(1 for s in slots
              if s.get("status") == "dontcare"
              or (s.get("status") == "filled" and s.get("basis") in ("stated", "interpreted")))


def reached_threshold(slots: List[Dict[str, Any]]) -> bool:
    return count_filled(slots) >= FILLED_THRESHOLD or count_touched(slots) >= TOUCHED_THRESHOLD


def slots_summary(slots: List[Dict[str, Any]]) -> str:
    """Tóm tắt slot để nhắc lại/lọc — CHỈ nêu thứ bắt nguồn TRỰC TIẾP từ lời khách
    (khách nói rõ, hoặc diễn giải thẳng câu khách như 'vừa phải'->inch). KHÔNG nêu slot
    đoán từ ngữ cảnh chung (mục đích/chân dung) mà khách chưa hề đề cập tới thông số đó."""
    parts = []
    for s in slots:
        if s.get("status") != "filled" or not s.get("value"):
            continue
        if s.get("basis") not in ("stated", "interpreted"):
            continue
        v = str(s["value"]).strip()
        parts.append(f"{s['name']}: {v}" + (" (em hiểu ý)" if s.get("basis") == "interpreted" else ""))
    return "; ".join(parts)


_SLOT_SYSTEM = (
    "Bạn là nhân viên tư vấn điện máy đang khoanh vùng nhu cầu khách cho ngành **{category}**.\n"
    "Các thông số có thể khai thác (slot): {columns}\n"
    "Slot đã biết từ các lượt trước (JSON): {current}\n\n"
    "Nhiệm vụ: đọc câu MỚI của khách và trả về TOÀN BỘ danh sách slot đã cập nhật + 1 câu hỏi tiếp theo.\n"
    "QUAN TRỌNG — chỉ điền slot khi có căn cứ TRỰC TIẾP từ lời khách:\n"
    "- Khách nêu rõ giá trị một thông số -> status='filled', basis='stated'.\n"
    "- DIỄN GIẢI trực tiếp chính câu khách vừa nói thành giá trị slot -> status='filled', "
    "basis='interpreted' (VD khách nói 'màn vừa phải' -> Kích thước màn hình ≈ 8-10 inch; "
    "'nhà 4 người' -> Số người sử dụng = 4).\n"
    "- TUYỆT ĐỐI KHÔNG tự đoán các thông số khách CHƯA đề cập chỉ vì mục đích/chân dung chung "
    "(VD khách nói 'học tập' thì KHÔNG được tự điền CPU, RAM, hệ điều hành, pin...). Cứ để 'unasked'.\n"
    "- Khách nói không biết/không quan trọng/tuỳ shop -> status='dontcare'.\n"
    "- Chưa đề cập -> status='unasked' (vẫn liệt kê để theo dõi).\n"
    "- Giữ nguyên slot đã 'filled'/'dontcare' ở lượt trước trừ khi khách đổi ý.\n"
    "next_question: MỘT câu hỏi tự nhiên, thân thiện cho slot quan trọng nhất còn 'unasked'. "
    "Có thể hỏi CHÂN DUNG (mua cho ai, giới tính, tuổi, mục đích) để KHAI THÁC thông tin — nhưng chỉ "
    "điền slot khi khách đã trả lời, không đoán trước. Không hỏi lại slot đã 'filled'/'dontcare'. "
    "Nếu không còn gì đáng hỏi -> null."
)


def update_slots(llm, query: str, history: List[Dict[str, str]], category: str,
                 spec_cols: List[str], current_slots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """AI cập nhật slot từ câu khách + soạn câu hỏi tiếp. Lỗi/không LLM -> giữ nguyên slot cũ."""
    if llm is None or not spec_cols:
        return {"slots": current_slots, "next_question": None}
    import json
    system = _SLOT_SYSTEM.format(
        category=category, columns=", ".join(spec_cols),
        current=json.dumps(current_slots, ensure_ascii=False) if current_slots else "[]")
    hist = "".join(f"{'User' if m.get('role') == 'user' else 'Assistant'}: {m.get('content')}\n"
                   for m in (history or [])[-6:])
    user = f"Lịch sử gần đây:\n{hist or 'Không có'}\n\nCâu mới của khách: {query}"
    try:
        raw = llm.complete_json(system, user, _SLOT_HINT)
    except Exception as e:
        log.warning("slots: LLM lỗi (%s) -> giữ slot cũ", e)
        return {"slots": current_slots, "next_question": None}
    slots = raw.get("slots") if isinstance(raw, dict) else None
    if not isinstance(slots, list):
        return {"slots": current_slots, "next_question": None}
    # Chỉ giữ slot có tên thuộc danh sách cột hợp lệ (chống bịa tên slot).
    valid = {c.lower(): c for c in spec_cols}
    clean = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        name = valid.get(str(s.get("name", "")).lower())
        if not name:
            continue
        status = s.get("status") if s.get("status") in ("filled", "dontcare", "unasked") else "unasked"
        basis = s.get("basis") if s.get("basis") in ("stated", "interpreted") else "stated"
        clean.append({"name": name, "value": s.get("value"), "status": status, "basis": basis})
    nq = raw.get("next_question") if isinstance(raw, dict) else None
    nq = nq.strip() if isinstance(nq, str) and nq.strip() else None
    log.info("slots: filled=%d touched=%d next_question=%r",
             count_filled(clean), count_touched(clean), (nq or "")[:60])
    return {"slots": clean, "next_question": nq}
