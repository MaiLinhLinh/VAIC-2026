from __future__ import annotations
from typing import Any, Dict, List
from app.schemas import ComparisonTable, ComparisonRow, ComparisonCell
from app.agent_core.presenters import product_display_name, load_specs, parse_leading_number
from app.advice.provenance import format_vnd

_MISSING = "chưa có dữ liệu"
# Suy hướng "tốt hơn" theo tên field (khớp chuỗi con).
_LOWER_BETTER = ("điện năng", "tiêu thụ", "độ ồn", "tiêu thụ nước")
_HIGHER_BETTER = ("dung tích", "pin", "bảo hành", "công suất", "tốc độ", "độ sáng", "bộ nhớ")


def _direction(field: str) -> str | None:
    f = field.lower()
    if any(k in f for k in _LOWER_BETTER):
        return "min"
    if any(k in f for k in _HIGHER_BETTER):
        return "max"
    return None


def _best_indices(nums: List[float | None], direction: str) -> set[int]:
    present = [(i, n) for i, n in enumerate(nums) if n is not None]
    if not present:
        return set()
    target = (min if direction == "min" else max)(n for _, n in present)
    return {i for i, n in present if n == target}


def _price_val(row: Dict[str, Any]) -> float | None:
    try:
        v = float(row.get("price_clean") or 0)
    except (ValueError, TypeError):
        return None
    return v if v > 0 else None


def build_comparison(rows: List[Dict[str, Any]], priority_features: List[str]) -> ComparisonTable | None:
    """Bảng so sánh side-by-side, mọi ô lấy trực tiếp từ DB (không qua LLM)."""
    from app.agent_core.presenters import product_display_name
    unique_rows = []
    seen = set()
    for r in rows:
        key = r.get("model_code") or r.get("sku") or product_display_name(r)
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)
            
    rows = unique_rows[:3]
    if len(rows) < 2:
        return None
    products = [product_display_name(r) for r in rows]
    out_rows: List[ComparisonRow] = []

    # 1) Giá — rẻ hơn tốt hơn
    prices = [_price_val(r) for r in rows]
    price_cells = [ComparisonCell(value=format_vnd(int(p)) if p is not None else _MISSING,
                                  available=p is not None) for p in prices]
    for i in _best_indices(prices, "min"):
        price_cells[i].is_best = True
    out_rows.append(ComparisonRow(label="Giá", unit=None, source="catalog",
                                  cells=price_cells, better="giá thấp hơn tốt hơn"))

    # 2) Spec số dùng chung — ưu tiên field khớp priority_features, rồi field xuất hiện nhiều nhất
    specs_per = [load_specs(r) for r in rows]
    field_count: Dict[str, int] = {}
    for sp in specs_per:
        for k, v in sp.items():
            if parse_leading_number(v) is not None:
                field_count[k] = field_count.get(k, 0) + 1
    prefs_low = [p.lower() for p in (priority_features or [])]

    def rank(field: str):
        pref_hit = any(p in field.lower() for p in prefs_low)
        return (0 if pref_hit else 1, -field_count[field])

    shared = sorted([f for f, c in field_count.items() if c >= 2], key=rank)[:4]
    for field in shared:
        nums = [parse_leading_number(sp.get(field)) for sp in specs_per]
        direction = _direction(field)
        cells = [ComparisonCell(value=(sp.get(field) if sp.get(field) else _MISSING),
                                available=sp.get(field) is not None) for sp in specs_per]
        if direction is not None:
            for i in _best_indices(nums, direction):
                cells[i].is_best = True
        better = ("chỉ số thấp hơn tốt hơn" if direction == "min"
                  else "chỉ số cao hơn tốt hơn" if direction == "max" else None)
        out_rows.append(ComparisonRow(label=field, unit=None, source="thông số nhà sản xuất",
                                       cells=cells, better=better))

    # 3) Thương hiệu (tham khảo)
    out_rows.append(ComparisonRow(label="Thương hiệu", unit=None, source="catalog",
                                  cells=[ComparisonCell(value=r.get("brand") or "N/A") for r in rows],
                                  better=None))
    return ComparisonTable(products=products, rows=out_rows)
