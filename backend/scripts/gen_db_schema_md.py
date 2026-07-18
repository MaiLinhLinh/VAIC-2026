"""Sinh file mô tả schema DB cho tool SQL của agent: app/agent_core/db_schema.md.

Đọc trực tiếp products.db để file luôn khớp thực tế (chạy lại sau mỗi lần rebuild DB):
    ./.venv/Scripts/python scripts/gen_db_schema_md.py
Mỗi cột kèm một giá trị ví dụ thật để model biết định dạng/đơn vị của dữ liệu.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3

from app.config import get_settings

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "agent_core", "db_schema.md")

HEADER = """# Schema CSDL products.db (SQLite) — SINH TỰ ĐỘNG bởi scripts/gen_db_schema_md.py

## Quy tắc đọc (quan trọng)
- `all_products`: 1 dòng = 1 sản phẩm (SKU), gộp mọi ngành. Cột `id` là khoá DUY NHẤT của dòng.
- `model_code` KHÔNG duy nhất (nhiều biến thể chung một mã) — không dùng làm khoá nhận diện.
- Giá bán (VND) là `price_clean`; giá trị 0/NULL nghĩa là CHƯA CÓ DỮ LIỆU giá, không phải miễn phí — muốn lọc/xếp theo giá phải kèm `price_clean > 0`.
- Mỗi ngành có bảng thông số riêng (1 dòng = 1 sản phẩm), JOIN với all_products qua `model_code`.
- Cột thông số là TEXT thường kèm đơn vị (vd '313 lít', '27 inch') — so sánh số bằng `CAST("tên cột" AS REAL)` (SQLite lấy phần số đứng đầu chuỗi).
- Tên cột tiếng Việt/có khoảng trắng phải bọc trong nháy kép: `"Dung tích tổng"`.
"""


def sample(conn, table: str, col: str):
    try:
        row = conn.execute(
            f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL '
            f'AND TRIM(CAST("{col}" AS TEXT)) NOT IN ("", "nan", "None") LIMIT 1').fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def describe(conn, table: str, title: str) -> str:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    lines = [f"\n## {title} ({n} dòng)"]
    for c in cols:
        v = sample(conn, table, c)
        v_txt = "" if v is None else f" — vd: {str(v)[:60]}"
        lines.append(f'- "{c}"{v_txt}')
    return "\n".join(lines)


def main():
    db = get_settings().agent_db_path
    conn = sqlite3.connect(db)
    parts = [HEADER, describe(conn, "all_products", "Bảng all_products (mọi ngành)")]
    cat_tables = conn.execute(
        "SELECT DISTINCT category, category_table FROM all_products "
        "WHERE category_table IS NOT NULL ORDER BY category_table").fetchall()
    for category, table in cat_tables:
        parts.append(describe(conn, table, f'Bảng "{table}" — ngành "{category}"'))
    conn.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(parts) + "\n")
    print(f"Đã ghi {OUT} ({os.path.getsize(OUT)} byte, {len(cat_tables)} bảng ngành)")


if __name__ == "__main__":
    main()
