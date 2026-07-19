"""Helper dựng DB SQLite tạm nhỏ cho test agent_core (không dùng products.db 35MB thật)."""
import json
import sqlite3


def make_db(path, rows):
    """rows: list dict có category (+ tuỳ chọn model_code, sku, brand, price_clean, gift_promo, specs)."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE all_products (id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_code TEXT, sku TEXT, category TEXT, category_table TEXT, brand TEXT,
        price_orig TEXT, price_promo TEXT, price_clean REAL, gift_promo TEXT,
        key_specs_summary TEXT, search_description TEXT, full_specs_json TEXT)""")
    for r in rows:
        specs = r.get("specs", {})
        summary = "; ".join(f"{k}: {v}" for k, v in list(specs.items())[:8])
        cur.execute("""INSERT INTO all_products
            (model_code, sku, category, category_table, brand, price_orig, price_promo,
             price_clean, gift_promo, key_specs_summary, search_description, full_specs_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("model_code", ""), r.get("sku", ""), r["category"], r.get("category_table", ""),
             r.get("brand", ""), r.get("price_orig", ""), r.get("price_promo", ""),
             r.get("price_clean"), r.get("gift_promo", ""), summary, summary,
             json.dumps(specs, ensure_ascii=False)))
    conn.commit()
    conn.close()
