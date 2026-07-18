"""Tool SQL cho agent: AI soạn câu SELECT — luật thẩm định, thi hành chỉ-đọc, có đường lui.

Phân vai giữ nguyên triết lý của luồng: AI chỉ quyết "chọn máy nào" (soạn truy vấn);
mọi con số tới khách vẫn đi qua fact card + verifier fail-closed như cũ, nên tool này
không mở thêm đường nào cho bịa số liệu. Kết quả truy vấn được nối ngược về bảng chuẩn
all_products qua model_code trước khi dựng card.
"""
from __future__ import annotations
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings

log = logging.getLogger("agent_core")

_MAX_ROWS = 20
# Số lần cho AI sửa lại câu lệnh (sau lần soạn đầu) khi bị từ chối/lỗi/0 dòng.
_MAX_REPAIRS = 3
_SCHEMA_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_schema.md")
_schema_md_cache: Optional[str] = None
_SELECT = re.compile(r"^\s*select\b", re.IGNORECASE)
_LIMIT = re.compile(r"\blimit\s+(\d+)", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(pragma|attach|detach|insert|update|delete|drop|alter|create|replace|vacuum|"
    r"trigger|reindex|analyze)\b", re.IGNORECASE)
_FROM_JOIN = re.compile(r'\b(?:from|join)\s+("[^"]+"|[\w]+)', re.IGNORECASE)

_SQL_HINT = '{"sql": string, "reason": string}'


def _resolve_db(db_path: Optional[str]) -> str:
    return db_path or get_settings().agent_db_path


def list_tables(db_path: Optional[str] = None) -> List[str]:
    conn = sqlite3.connect(_resolve_db(db_path))
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        conn.close()
    return [n for n in names if n != "sqlite_sequence"]


def _load_schema_md() -> Optional[str]:
    global _schema_md_cache
    if _schema_md_cache is None and os.path.exists(_SCHEMA_MD):
        with open(_SCHEMA_MD, encoding="utf-8") as f:
            _schema_md_cache = f.read()
    return _schema_md_cache


def schema_for_prompt(db_path: Optional[str] = None, category_table: Optional[str] = None) -> str:
    """Mô tả schema cho AI, ưu tiên file db_schema.md (sinh bởi scripts/gen_db_schema_md.py).

    Đã rõ ngành -> chỉ chèn phần quy tắc + all_products + bảng ngành đó (tiết kiệm token);
    chưa rõ ngành -> chèn cả file. Không có file -> tự dựng tối thiểu từ PRAGMA."""
    md = _load_schema_md()
    if md:
        if category_table:
            sections = md.split("\n## ")
            keep = [sections[0]]  # phần quy tắc đầu file
            for sec in sections[1:]:
                if sec.startswith("Bảng all_products") or f'"{category_table}"' in sec.split("\n", 1)[0]:
                    keep.append("## " + sec)
            return "\n".join(keep)
        return md
    # Đường lui khi chưa sinh file md: dựng tối thiểu từ PRAGMA.
    db = _resolve_db(db_path)
    conn = sqlite3.connect(db)
    try:
        main_cols = [r[1] for r in conn.execute("PRAGMA table_info(all_products)")]
        lines = [f"Bảng all_products (mọi ngành, 1 dòng = 1 sản phẩm): {', '.join(main_cols)}"]
        if category_table and category_table in list_tables(db):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{category_table}")')]
            lines.append(f'Bảng "{category_table}" (thông số riêng ngành, 1 dòng = 1 sản phẩm): '
                         + ", ".join(f'"{c}"' for c in cols))
    finally:
        conn.close()
    lines.append(
        "Giá bán (VND) là cột price_clean của all_products (0/NULL = chưa có giá). "
        "Cột thông số là text kèm đơn vị (vd '313 lít', '27 inch') — so sánh số bằng "
        'CAST("tên cột" AS REAL); tên cột tiếng Việt phải đặt trong nháy kép.')
    return "\n".join(lines)


def validate_sql(sql: str, db_path: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Thẩm định câu lệnh AI soạn. Trả (sql chuẩn hoá, None) nếu đạt, (sql, lý do) nếu từ chối."""
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        return s, "câu lệnh rỗng"
    if ";" in s:
        return s, "chỉ được đúng một câu lệnh"
    if not _SELECT.match(s):
        return s, "chỉ được SELECT"
    if _FORBIDDEN.search(s):
        return s, "chứa từ khoá bị cấm"
    allowed = {t.lower() for t in list_tables(db_path)}
    for m in _FROM_JOIN.finditer(s):
        name = m.group(1).strip('"').lower()
        if name not in allowed:
            return s, f"bảng không được phép: {name}"
    m = _LIMIT.search(s)
    if not m:
        s += f" LIMIT {_MAX_ROWS}"
    elif int(m.group(1)) > _MAX_ROWS:
        s = _LIMIT.sub(f"LIMIT {_MAX_ROWS}", s)
    return s, None


def run_sql_readonly(sql: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Thi hành trên kết nối CHỈ-ĐỌC (mode=ro): kể cả lọt lưới thẩm định cũng không ghi được gì."""
    db = _resolve_db(db_path)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        return [dict(r) for r in cur.fetchmany(_MAX_ROWS)]
    finally:
        conn.close()


def agent_query(llm, user_query: str, intent: Dict[str, Any],
                category_table: Optional[str] = None,
                db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """AI soạn SELECT theo nhu cầu -> thẩm định -> chạy chỉ-đọc. Câu bị từ chối/lỗi/ra 0 dòng
    được đưa lại cho AI sửa (tối đa _MAX_REPAIRS lần); vẫn hỏng -> None (caller rơi về khuôn cũ)."""
    if llm is None:
        return None
    schema = schema_for_prompt(db_path, category_table)
    system = (
        "Bạn là chuyên gia SQLite cho catalog điện máy. Soạn MỘT câu SELECT trả về các "
        "SẢN PHẨM khớp nhu cầu khách.\n"
        f"{schema}\n"
        "QUY TẮC:\n"
        "- Chỉ SELECT, một câu duy nhất, LIMIT tối đa 20.\n"
        "- Kết quả PHẢI có cột model_code (an toàn nhất: SELECT *). KHÔNG GROUP BY/tính gộp.\n"
        "- Cần xếp theo giá thì lọc price_clean > 0 trước (0/NULL là chưa có giá, không phải miễn phí).\n"
        "- Không bịa tên bảng/cột ngoài schema ở trên.\n"
        "- TUYỆT ĐỐI không nới ngân sách hay ràng buộc khách đã nêu; nếu vì thế mà không có "
        "sản phẩm nào thì chấp nhận trả 0 dòng (hệ thống sẽ tự xử lý phần tư vấn nới ngân sách).\n"
        "- Nếu khách yêu cầu 'càng rẻ càng tốt', 'rẻ nhất' hoặc 'giá rẻ', BẮT BUỘC thêm mệnh đề ORDER BY price_clean ASC."
    )
    user = (f"Nhu cầu khách: {user_query}\n"
            f"Phiếu nhu cầu đã trích: category={intent.get('category')!r}, "
            f"budget_max={intent.get('budget_max')}, brand={intent.get('brand')!r}, "
            f"priority={intent.get('priority_features')}\n"
            "Soạn SQL chọn sản phẩm phù hợp nhất.")
    err_note = ""
    last_zero_sql: Optional[str] = None
    for attempt in range(1 + _MAX_REPAIRS):
        try:
            raw = llm.complete_json(system, user + err_note, _SQL_HINT)
        except Exception as e:
            log.warning("sql_tool: LLM lỗi (%s) -> bỏ qua tool", e)
            return None
        sql, reject = validate_sql((raw or {}).get("sql", ""), db_path)
        if reject:
            log.warning("sql_tool: lần %d bị từ chối (%s): %s", attempt + 1, reject, sql[:150])
            err_note = f"\n\nCâu trước bị từ chối ({reject}):\n{sql}\nHãy sửa lại."
            continue
        try:
            rows = run_sql_readonly(sql, db_path)
        except sqlite3.Error as e:
            log.warning("sql_tool: lần %d lỗi thi hành (%s): %s", attempt + 1, e, sql[:150])
            err_note = f"\n\nCâu trước lỗi SQLite ({e}):\n{sql}\nHãy sửa lại."
            continue
        if not rows and attempt < _MAX_REPAIRS:
            if sql == last_zero_sql:
                # AI giữ nguyên câu cũ = khẳng định truy vấn đúng, thật sự không có hàng.
                log.info("sql_tool: AI giữ nguyên câu 0 dòng -> chấp nhận kết quả rỗng")
                return {"rows": [], "sql": sql}
            last_zero_sql = sql
            # 0 dòng cũng coi là "truy xuất sai" cho tới lần sửa cuối: thường do sai tên
            # cột/đơn vị hoặc điều kiện quá chặt — cho AI xem lại.
            log.info("sql_tool: lần %d ra 0 dòng, cho AI sửa | sql=%s", attempt + 1, sql[:150])
            err_note = (f"\n\nCâu trước chạy được nhưng trả về 0 dòng:\n{sql}\n"
                        "Chỉ sửa nếu nghi sai TÊN CỘT/ĐƠN VỊ/cú pháp so sánh; TUYỆT ĐỐI không nới "
                        "ngân sách hay ràng buộc khách đã nêu — nếu đã đúng hết thì trả lại y nguyên "
                        "câu cũ (hệ thống chấp nhận 0 dòng).")
            continue
        log.info("sql_tool: OK %d dòng (lần %d) | sql=%s", len(rows), attempt + 1, sql)
        return {"rows": rows, "sql": sql}
    return None
