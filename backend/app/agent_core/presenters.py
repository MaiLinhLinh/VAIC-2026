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
    return FactCard(title=f"Vì sao em đề xuất {name}?", lines=lines, missing=missing)


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
    return FactCard(title=f"Thông tin chi tiết: {name}", lines=lines, missing=missing)
