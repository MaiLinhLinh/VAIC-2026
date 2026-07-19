from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from app.schemas import FactCard, FactLine
from app.advice.provenance import format_vnd

# Các mục dataset gốc không có -> luôn báo "chưa có dữ liệu".
_ALWAYS_MISSING = ["tồn kho", "đánh giá người dùng (review)", "trả góp"]
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_leading_number(s: Any) -> float | None:
    if s is None:
        return None
    m = _NUM.search(str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def load_specs(row: Dict[str, Any]) -> Dict[str, str]:
    raw = row.get("full_specs_json") or "{}"
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return {str(k): str(v) for k, v in d.items() if str(v).strip() not in ("", "nan", "None")}


def product_display_name(row: Dict[str, Any]) -> str:
    brand = (row.get("brand") or "").strip()
    code = (row.get("model_code") or "").strip() or (row.get("sku") or "").strip()
    if code and not code.upper().startswith("SKU-"):
        return f"{brand} {code}".strip()
    summary = (row.get("key_specs_summary") or "").strip()
    if brand and summary:
        return f"{brand} - {summary[:40]}"
    return brand or summary[:40] or "Sản phẩm"


def _price_value(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("price_clean") or 0)
    except (ValueError, TypeError):
        return 0.0


def build_reco_card(row: Dict[str, Any], priority_features: List[str]) -> FactCard:
    """Card 'vì sao đề xuất': giá + hãng + vài spec liên quan ưu tiên của khách; mọi dòng gắn nguồn."""
    name = product_display_name(row)
    lines: List[FactLine] = []
    missing: List[str] = []
    price = _price_value(row)
    if price > 0:
        lines.append(FactLine(label="Giá", value=format_vnd(int(price)), source="catalog"))
    else:
        missing.append("giá")
    lines.append(FactLine(label="Thương hiệu", value=row.get("brand") or "N/A", source="catalog"))
    
    specs = load_specs(row)
    if specs.get("rating (crawl)"):
        lines.append(FactLine(label="Đánh giá", value=f"{specs['rating (crawl)']} sao", source="dienmayxanh"))
    if specs.get("url (crawl)"):
        lines.append(FactLine(label="Link sản phẩm", value=str(specs["url (crawl)"]), source="catalog"))
    prefs_low = [p.lower() for p in (priority_features or [])]
    # Ưu tiên hiển thị: (1) spec khớp ưu tiên khách, (2) spec CÓ SỐ (để LLM có con số hợp lệ
    # để trích dẫn, giảm fail-closed), (3) spec còn lại. Tối đa 5 dòng spec.
    ordered = sorted(
        specs.items(),
        key=lambda kv: (
            0 if any(p in kv[0].lower() or p in kv[1].lower() for p in prefs_low) else 1,
            0 if parse_leading_number(kv[1]) is not None else 1,
        ),
    )
    for k, v in ordered[:5]:
        lines.append(FactLine(label=k, value=v, source="thông số nhà sản xuất"))

    if row.get("gift_promo"):
        lines.append(FactLine(label="Khuyến mãi/quà kèm", value=str(row["gift_promo"]),
                               source="khuyến mãi (catalog)"))
    missing.extend(_ALWAYS_MISSING)

    # Extract productidweb
    productidweb = row.get("productidweb")
    if not productidweb:
        table_name = row.get("category_table")
        sku = row.get("sku")
        model_code = row.get("model_code")
        if table_name and (sku or model_code):
            import sqlite3
            from app.config import get_settings
            try:
                conn = sqlite3.connect(get_settings().agent_db_path)
                cursor = conn.cursor()
                row_db = None
                if sku:
                    cursor.execute(f"SELECT productidweb FROM {table_name} WHERE sku = ?", (sku,))
                    row_db = cursor.fetchone()
                if (row_db is None or row_db[0] is None) and model_code:
                    cursor.execute(f"SELECT productidweb FROM {table_name} WHERE model_code = ?", (model_code,))
                    row_db = cursor.fetchone()
                if row_db and row_db[0] is not None:
                    productidweb = row_db[0]
                conn.close()
            except Exception:
                pass

    if productidweb is not None:
        productidweb = str(productidweb).strip()
        if productidweb.endswith('.0'):
            productidweb = productidweb[:-2]
        if productidweb.lower() in ('nan', 'none', 'null', ''):
            productidweb = None

    product_link, image_url = None, None
    if productidweb:
        from app.advice.crawler import fetch_product_info
        product_link, image_url = fetch_product_info(productidweb)

    card = FactCard(title=f"Vì sao em đề xuất {name}?", lines=lines, missing=missing,
                    productidweb=productidweb, image_url=image_url, product_link=product_link)
    from app.advice.crawler import enrich_card_with_detail
    enrich_card_with_detail(card)
    return card


def build_detail_card(row: Dict[str, Any]) -> FactCard:
    """Fact-sheet đầy đủ 1 sản phẩm: giá + TOÀN BỘ spec + quà; mọi dòng gắn nguồn."""
    name = product_display_name(row)
    lines: List[FactLine] = []
    missing: List[str] = []
    price = _price_value(row)
    if price > 0:
        lines.append(FactLine(label="Giá", value=format_vnd(int(price)), source="catalog"))
    else:
        missing.append("giá")
    lines.append(FactLine(label="Thương hiệu", value=row.get("brand") or "N/A", source="catalog"))
    
    specs = load_specs(row)
    if specs.get("rating (crawl)"):
        lines.append(FactLine(label="Đánh giá", value=f"{specs['rating (crawl)']} sao", source="dienmayxanh"))
    if specs.get("url (crawl)"):
        lines.append(FactLine(label="Link sản phẩm", value=str(specs["url (crawl)"]), source="catalog"))
    
    for k, v in specs.items():
        lines.append(FactLine(label=k, value=v, source="thông số nhà sản xuất"))
    if row.get("gift_promo"):
        lines.append(FactLine(label="Khuyến mãi/quà kèm", value=str(row["gift_promo"]),
                               source="khuyến mãi (catalog)"))
    missing.extend(_ALWAYS_MISSING)

    # Extract productidweb
    productidweb = row.get("productidweb")
    if not productidweb:
        table_name = row.get("category_table")
        sku = row.get("sku")
        model_code = row.get("model_code")
        if table_name and (sku or model_code):
            import sqlite3
            from app.config import get_settings
            try:
                conn = sqlite3.connect(get_settings().agent_db_path)
                cursor = conn.cursor()
                row_db = None
                if sku:
                    cursor.execute(f"SELECT productidweb FROM {table_name} WHERE sku = ?", (sku,))
                    row_db = cursor.fetchone()
                if (row_db is None or row_db[0] is None) and model_code:
                    cursor.execute(f"SELECT productidweb FROM {table_name} WHERE model_code = ?", (model_code,))
                    row_db = cursor.fetchone()
                if row_db and row_db[0] is not None:
                    productidweb = row_db[0]
                conn.close()
            except Exception:
                pass

    if productidweb is not None:
        productidweb = str(productidweb).strip()
        if productidweb.endswith('.0'):
            productidweb = productidweb[:-2]
        if productidweb.lower() in ('nan', 'none', 'null', ''):
            productidweb = None

    product_link, image_url = None, None
    if productidweb:
        from app.advice.crawler import fetch_product_info
        product_link, image_url = fetch_product_info(productidweb)

    card = FactCard(title=f"Thông tin chi tiết: {name}", lines=lines, missing=missing,
                    productidweb=productidweb, image_url=image_url, product_link=product_link)
    from app.advice.crawler import enrich_card_with_detail
    enrich_card_with_detail(card)
    return card
