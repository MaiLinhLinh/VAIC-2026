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


def _normalize_pid(raw: Any) -> str | None:
    if raw is None:
        return None
    pid = str(raw).strip()
    if pid.endswith(".0"):
        pid = pid[:-2]
    if not pid or pid.lower() in ("nan", "none", "null"):
        return None
    return pid


def _lookup_web_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    """Lấy productidweb + link/ảnh/rating đã cào sẵn từ bảng danh mục trong products.db.

    DB là nguồn chính (prod không gọi được dienmayxanh.com); chỉ khi DB thiếu
    link/ảnh mới fallback cào trực tiếp."""
    meta: Dict[str, Any] = {"productidweb": _normalize_pid(row.get("productidweb")),
                            "link": None, "image": None, "rating": None}
    table_name = row.get("category_table")
    sku = row.get("sku")
    model_code = row.get("model_code")
    if table_name and (sku or model_code):
        import sqlite3
        from app.config import get_settings
        try:
            conn = sqlite3.connect(get_settings().agent_db_path)
            cursor = conn.cursor()
            select = (f'SELECT productidweb, "url (crawl)", "ảnh (crawl)", "rating (crawl)" '
                      f'FROM {table_name}')
            row_db = None
            if sku:
                cursor.execute(f"{select} WHERE sku = ?", (sku,))
                row_db = cursor.fetchone()
            if (row_db is None or row_db[0] is None) and model_code:
                cursor.execute(f"{select} WHERE model_code = ?", (model_code,))
                row_db = cursor.fetchone()
            conn.close()
            if row_db:
                meta["productidweb"] = meta["productidweb"] or _normalize_pid(row_db[0])
                link, image, rating = row_db[1], row_db[2], row_db[3]
                if link and str(link).startswith("http"):
                    meta["link"] = str(link)
                if image and str(image).startswith("http"):
                    meta["image"] = str(image)
                try:
                    if rating is not None and str(rating).strip() not in ("", "nan"):
                        meta["rating"] = float(rating)
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

    if meta["productidweb"] and not (meta["link"] and meta["image"]):
        from app.advice.crawler import fetch_product_info
        link, image = fetch_product_info(meta["productidweb"])
        meta["link"] = meta["link"] or link
        meta["image"] = meta["image"] or image
    return meta


def _apply_db_rating(card: FactCard, rating: float | None) -> None:
    """Nếu crawl runtime không lấy được đánh giá, dùng rating đã lưu trong DB."""
    if rating is None or card.rating is not None:
        return
    card.rating = rating
    card.lines.append(FactLine(label="Đánh giá", value=f"{rating}/5", source="dienmayxanh.com"))
    card.missing = [m for m in card.missing if m != "đánh giá người dùng (review)"]


def build_reco_card(row: Dict[str, Any], priority_features: List[str], self_term: str = "em") -> FactCard:
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
    if row.get("url"):
        lines.append(FactLine(label="Link sản phẩm", value=str(row["url"]), source="catalog"))

    specs = load_specs(row)
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

    meta = _lookup_web_meta(row)
    card = FactCard(title=f"Vì sao {self_term} đề xuất {name}?", lines=lines, missing=missing,
                    productidweb=meta["productidweb"], image_url=meta["image"],
                    product_link=meta["link"])
    from app.advice.crawler import enrich_card_with_detail
    enrich_card_with_detail(card)
    _apply_db_rating(card, meta["rating"])
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
    if row.get("url"):
        lines.append(FactLine(label="Link sản phẩm", value=str(row["url"]), source="catalog"))
    for k, v in load_specs(row).items():
        lines.append(FactLine(label=k, value=v, source="thông số nhà sản xuất"))
    if row.get("gift_promo"):
        lines.append(FactLine(label="Khuyến mãi/quà kèm", value=str(row["gift_promo"]),
                               source="khuyến mãi (catalog)"))
    missing.extend(_ALWAYS_MISSING)

    meta = _lookup_web_meta(row)
    card = FactCard(title=f"Thông tin chi tiết: {name}", lines=lines, missing=missing,
                    productidweb=meta["productidweb"], image_url=meta["image"],
                    product_link=meta["link"])
    from app.advice.crawler import enrich_card_with_detail
    enrich_card_with_detail(card)
    _apply_db_rating(card, meta["rating"])
    return card
