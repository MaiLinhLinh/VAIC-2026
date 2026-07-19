"""Slot theo ngành hàng — thu thập dần các thông số ĐẶC THÙ của ngành.

Slot lấy trực tiếp từ CỘT thông số của bảng ngành trong DB (không hardcode). Mỗi slot có
trạng thái: 'filled' (có thông tin — khách nói hoặc AI giả định từ chân dung), 'dontcare'
(khách nói không biết/không quan trọng), 'unasked' (chưa đề cập). AI theo dõi + cập nhật
slot mỗi lượt và chọn cột thông số tiếp theo cần hỏi.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.agent_core.search_description import (
    description_fields_for_table,
    order_description_fields,
)

log = logging.getLogger("agent_core")

# Ngưỡng chuyển sang bước xác nhận so sánh (tính theo từng ngành, không phải toàn phiên).
FILLED_THRESHOLD = 3   # >= 3 slot CÓ THÔNG TIN thật
TOUCHED_THRESHOLD = 6  # hoặc >= 6 slot đã xác định (gồm cả 'không biết')

_SLOT_HINT = ('{"slots": [{"name": string, "value": string|null, '
              '"status": "filled"|"dontcare"|"unasked", '
              '"basis": "stated"|"interpreted", "hard": bool}]}')

def _resolve_db(db_path: Optional[str]) -> str:
    return db_path or get_settings().agent_db_path


def spec_slot_columns(category_table: str, db_path: Optional[str] = None) -> List[str]:
    """Các cột thông số có thể hỏi.

    Ngoài việc loại cột kỹ thuật/giá, chỉ giữ cột có dữ liệu ở ít nhất 25% sản phẩm
    của ngành. Nhờ vậy trợ lý không hỏi một tiêu chí mà catalog hầu như không thể
    dùng để kiểm chứng hoặc lọc.
    """
    if not category_table:
        return []
    return list(description_fields_for_table(_resolve_db(db_path), category_table))


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
    "Nhiệm vụ: đọc câu MỚI của khách và trả về TOÀN BỘ danh sách slot đã cập nhật.\n"
    "QUAN TRỌNG — chỉ điền slot khi có căn cứ TRỰC TIẾP từ lời khách:\n"
    "- Khách nêu rõ giá trị một thông số -> status='filled', basis='stated'.\n"
    "- DIỄN GIẢI trực tiếp chính câu khách vừa nói thành giá trị slot -> status='filled', "
    "basis='interpreted' (VD khách nói 'màn vừa phải' -> Kích thước màn hình ≈ 8-10 inch; "
    "'nhà 4 người' -> Số người sử dụng = 4).\n"
    "- TUYỆT ĐỐI KHÔNG tự đoán các thông số khách CHƯA đề cập chỉ vì mục đích/chân dung chung "
    "(VD khách nói 'học tập' thì KHÔNG được tự điền CPU, RAM, hệ điều hành, pin...). Cứ để 'unasked'.\n"
    "- Khách nói không biết/không quan trọng/tuỳ shop -> status='dontcare'.\n"
    "- hard=true khi khách nêu đây là điều kiện bắt buộc hoặc gọi rõ một loại/công nghệ cần tìm "
    "(VD 'máy in laser', 'phải có Wi-Fi'). hard=false nếu chỉ là sở thích mềm.\n"
    "- Chưa đề cập -> status='unasked' (vẫn liệt kê để theo dõi).\n"
    "- Giữ nguyên slot đã 'filled'/'dontcare' ở lượt trước trừ khi khách đổi ý.\n"
    "Không soạn câu hỏi tiếp theo; hệ thống sẽ tự chọn cột từ schema DB."
)


def next_description_question(spec_cols: List[str], slots: List[Dict[str, Any]],
                              limit: int = 3) -> Dict[str, Any]:
    """Chọn 2-3 cột chưa hỏi và dựng câu hỏi mà không gọi LLM."""
    touched = {str(s.get("name", "")).lower() for s in slots
               if s.get("status") in ("filled", "dontcare")}
    candidates = [name for name in order_description_fields(spec_cols)
                  if name.lower() not in touched][:max(1, min(3, limit))]
    labels = [name.lower() for name in candidates]
    if len(labels) > 1:
        label_text = ", ".join(labels[:-1]) + " và " + labels[-1]
    else:
        label_text = labels[0] if labels else ""
    question = (f"Về {label_text}, anh/chị có yêu cầu cụ thể nào không ạ?"
                if label_text else None)
    return {"next_slots": candidates, "next_question": question}


def merge_slot_updates(spec_cols: List[str], current_slots: List[Dict[str, Any]],
                       updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gộp các slot do lượt intent LLM trích, nhưng chỉ nhận cột có thật trong DB.

    Intent và slot dùng chung một LLM call để tránh thêm một round-trip. Hàm này là
    lớp chặn tất định: model chỉ được cập nhật dữ kiện khách vừa nói, không thể tạo
    tên cột mới hoặc làm mất một điều kiện bắt buộc đã ghi nhận trước đó.
    """
    valid = {name.lower(): name for name in spec_cols}
    merged = {
        str(slot.get("name", "")).lower(): dict(slot)
        for slot in current_slots
        if isinstance(slot, dict) and str(slot.get("name", "")).lower() in valid
    }
    for update in updates or []:
        if not isinstance(update, dict):
            continue
        key = str(update.get("name", "")).lower()
        name = valid.get(key)
        status = update.get("status")
        if not name or status not in ("filled", "dontcare"):
            continue
        previous = merged.get(key, {})
        basis = update.get("basis")
        if basis not in ("stated", "interpreted"):
            basis = "stated"
        value = update.get("value") if status == "filled" else None
        if status == "filled" and (value is None or not str(value).strip()):
            continue
        merged[key] = {
            "name": name,
            "value": value,
            "status": status,
            "basis": basis,
            "hard": bool(previous.get("hard")) or bool(update.get("hard")),
        }
    return list(merged.values())


def update_slots(llm, query: str, history: List[Dict[str, str]], category: str,
                 spec_cols: List[str], current_slots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """AI chỉ cập nhật slot; câu hỏi tiếp theo được dựng tất định từ schema."""
    if llm is None or not spec_cols:
        return {"slots": current_slots, **next_description_question(spec_cols, current_slots)}
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
        return {"slots": current_slots, **next_description_question(spec_cols, current_slots)}
    slots = raw.get("slots") if isinstance(raw, dict) else None
    if not isinstance(slots, list):
        return {"slots": current_slots, **next_description_question(spec_cols, current_slots)}
    # Chỉ giữ slot có tên thuộc danh sách cột hợp lệ (chống bịa tên slot).
    valid = {c.lower(): c for c in spec_cols}
    previous_by_name = {str(s.get("name", "")).lower(): s for s in current_slots
                        if isinstance(s, dict)}
    clean = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        name = valid.get(str(s.get("name", "")).lower())
        if not name:
            continue
        status = s.get("status") if s.get("status") in ("filled", "dontcare", "unasked") else "unasked"
        basis = s.get("basis") if s.get("basis") in ("stated", "interpreted") else "stated"
        was_hard = bool(previous_by_name.get(name.lower(), {}).get("hard"))
        clean.append({"name": name, "value": s.get("value"), "status": status, "basis": basis,
                      # Điều kiện bắt buộc không được tự biến thành sở thích mềm ở lượt sau.
                      "hard": was_hard or bool(s.get("hard", False))})
    returned_names = {s["name"].lower() for s in clean}
    for old in current_slots:
        old_name = valid.get(str(old.get("name", "")).lower()) if isinstance(old, dict) else None
        if old_name and old_name.lower() not in returned_names:
            # Model phải trả toàn bộ slot, nhưng nếu lỡ bỏ sót thì giữ dữ kiện cũ thay vì mất
            # một ràng buộc bắt buộc đã được khách nêu ở lượt trước.
            clean.append({**old, "name": old_name})
    question = next_description_question(spec_cols, clean)
    nq = question["next_question"]
    log.info("slots: filled=%d touched=%d next_question=%r",
             count_filled(clean), count_touched(clean), (nq or "")[:60])
    return {"slots": clean, **question}
