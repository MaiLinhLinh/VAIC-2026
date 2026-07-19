import sys
import os
import re
import json
import sqlite3
import pandas as pd
import openpyxl

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from app.config import get_settings
from app.agent_core.search_description import (
    DESCRIPTION_COLUMN,
    build_search_description,
    select_description_fields,
)
_S = get_settings()
EXCEL_PATH = _S.excel_source_path
DB_PATH = _S.agent_db_path

SHEET_TO_TABLE_MAP = {
    "Tủ Lạnh": "tu_lanh",
    "Máy lạnh": "may_lanh",
    "Máy giặt": "may_giat",
    "Máy sấy quần áo": "may_say_quan_ao",
    "Máy rửa chén": "may_rua_chen",
    "Tủ mát, tủ đông": "tu_mat_tu_dong",
    "Máy nước nóng": "may_nuoc_nong",
    "Micro karaoke": "micro_karaoke",
    "Micro thu âm điện thoại": "micro_thu_am",
    "Đồng hồ thông minh": "dong_ho_thong_minh",
    "Máy tính để bàn": "may_tinh_de_ban",
    "Màn hình máy tính": "man_hinh_may_tinh",
    "Máy in": "may_in",
    "Máy tính bảng": "may_tinh_bang"
}

def clean_price_number(val):
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ['nan', 'none', 'null', 'không công bố']:
        return None
    # Remove currency symbols, commas, spaces
    cleaned = re.sub(r'[^\d.]', '', s)
    try:
        if '.' in cleaned:
            return float(cleaned)
        return float(int(cleaned))
    except Exception:
        return None

def clean_capacity_number(val):
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    # Extract first integer or decimal
    match = re.search(r'(\d+(?:\.\d+)?)', s)
    if match:
        try:
            return float(match.group(1))
        except Exception:
            return None
    return None

def ingest_data():
    if not os.path.exists(EXCEL_PATH):
        raise FileNotFoundError(f"Không tìm thấy file Excel tại {EXCEL_PATH}")

    print(f"Bắt đầu xử lý dữ liệu từ: {EXCEL_PATH}")
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create unified table for quick searching across all categories
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS all_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_code TEXT,
        sku TEXT,
        category TEXT,
        category_table TEXT,
        brand TEXT,
        price_orig TEXT,
        price_promo TEXT,
        price_clean REAL,
        gift_promo TEXT,
        key_specs_summary TEXT,
        search_description TEXT,
        full_specs_json TEXT
    )
    """)

    xls = pd.ExcelFile(EXCEL_PATH)
    total_products = 0

    for sheet_name in xls.sheet_names:
        if sheet_name not in SHEET_TO_TABLE_MAP:
            print(f"Bỏ qua sheet không rõ: {sheet_name}")
            continue

        table_name = SHEET_TO_TABLE_MAP[sheet_name]
        print(f"Đang xử lý sheet: {sheet_name} -> bảng: {table_name}")
        df = pd.read_excel(xls, sheet_name=sheet_name)

        # Normalize column names
        df.columns = [str(col).strip() for col in df.columns]

        # Clean string columns
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.strip().replace({'nan': '', 'None': '', 'null': ''})

        # Add cleaned numeric columns for accurate SQL querying
        if 'giá hiệu lực' in df.columns:
            df['price_promo_clean'] = df['giá hiệu lực'].apply(clean_price_number)
        elif 'giá khuyến mãi' in df.columns:
            df['price_promo_clean'] = df['giá khuyến mãi'].apply(clean_price_number)
        elif 'giá gốc' in df.columns:
            df['price_promo_clean'] = df['giá gốc'].apply(clean_price_number)
        else:
            df['price_promo_clean'] = None

        if 'Dung tích tổng' in df.columns:
            df['capacity_clean'] = df['Dung tích tổng'].apply(clean_capacity_number)
        elif 'Dung tích sử dụng' in df.columns:
            df['capacity_clean'] = df['Dung tích sử dụng'].apply(clean_capacity_number)
        elif 'Khối lượng tải chính' in df.columns:
            df['capacity_clean'] = df['Khối lượng tải chính'].apply(clean_capacity_number)
        else:
            df['capacity_clean'] = None

        # Write table to SQLite
        df.to_sql(table_name, conn, if_exists='replace', index=False)

        # Chọn cột mô tả một lần cho cả ngành: đủ độ phủ, không chứa ID/giá/khuyến mãi.
        description_fields = select_description_fields(df.to_dict(orient='records'))

        # Populate unified table
        for idx, row in df.iterrows():
            model_code = str(row.get('model_code', '')).strip()
            if not model_code or model_code.lower() == 'nan':
                model_code = f"SKU-{row.get('sku', idx)}"
            
            sku = str(row.get('sku', '')).strip()
            brand = str(row.get('brand', row.get('brand_id', ''))).strip()
            price_orig = str(row.get('giá gốc', '')).strip()
            price_promo = str(row.get('giá khuyến mãi', '')).strip()
            price_clean = row.get('price_promo_clean')
            if pd.isna(price_clean):
                price_clean = clean_price_number(price_orig)
            
            gift_promo = str(row.get('khuyến mãi quà', '')).strip()

            # Build dict of specs excluding ID columns and internal clean columns
            specs_dict = {}
            for col in df.columns:
                if col not in ['model_code', 'sku', 'productidweb', 'category_code', 'brand_id', 'brand',
                               'price_promo_clean', 'capacity_clean', DESCRIPTION_COLUMN]:
                    val = row[col]
                    if pd.notna(val) and str(val).strip() not in ['', 'nan', 'None']:
                        specs_dict[col] = str(val).strip()

            full_specs_json = json.dumps(specs_dict, ensure_ascii=False)
            
            # Key summary for quick display and semantic scoring
            summary_items = [f"{k}: {v}" for k, v in list(specs_dict.items())[:8]]
            key_specs_summary = "; ".join(summary_items)
            search_description = str(row.get(DESCRIPTION_COLUMN, '') or '').strip()
            if not search_description or search_description.lower() == 'nan':
                search_description = build_search_description(
                    sheet_name, brand, specs_dict, description_fields
                )

            cursor.execute("""
            INSERT INTO all_products (model_code, sku, category, category_table, brand, price_orig, price_promo, price_clean, gift_promo, key_specs_summary, search_description, full_specs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (model_code, sku, sheet_name, table_name, brand, price_orig, price_promo,
                  price_clean, gift_promo, key_specs_summary, search_description, full_specs_json))

            total_products += 1

    cursor.execute("""CREATE INDEX IF NOT EXISTS idx_all_products_category_price
                      ON all_products(category, price_clean)""")
    conn.commit()
    conn.close()
    print(f"Hoàn tất! Đã nạp tổng cộng {total_products} sản phẩm vào cơ sở dữ liệu {DB_PATH}.")

if __name__ == '__main__':
    ingest_data()
