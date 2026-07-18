from __future__ import annotations
from app.schemas import ScoredProduct, NeedProfile, ComparisonCell, ComparisonRow, ComparisonTable
from app.catalog.category_config import config_for
from app.advice.provenance import format_vnd

_MISSING = "chưa có dữ liệu"


def _fmt_spec(value, unit: str | None) -> str:
    v = int(value) if isinstance(value, float) and value.is_integer() else value
    return f"{v} {unit}" if unit else f"{v}"


def _best_indices(nums: list[float | None], direction: str) -> set[int]:
    """Chỉ số các ứng viên tốt nhất (đánh dấu CẢ khi hòa điểm), chỉ tính trên ô CÓ dữ liệu."""
    present = [(i, n) for i, n in enumerate(nums) if n is not None]
    if not present:
        return set()
    target = (min if direction == "min" else max)(n for _, n in present)
    return {i for i, n in present if n == target}


def _decision_fields(profile: NeedProfile) -> list[tuple[str, str, str | None]]:
    """(field, direction, unit) suy từ đúng các ưu tiên khách nêu — dimension so sánh."""
    if not profile.category:
        return []
    cfg = config_for(profile.category)
    units = {sd.field: sd.unit for sd in cfg.specs}
    out: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for pref in profile.prefs:
        for sig in cfg.pref_lexicon.get(pref, []):
            if sig.field not in seen:
                seen.add(sig.field)
                out.append((sig.field, sig.direction, units.get(sig.field)))
    return out


def _numeric_row(label: str, unit: str | None, source: str, nums: list[float | None],
                 direction: str | None, better: str | None) -> ComparisonRow:
    cells = [ComparisonCell(value=_fmt_spec(n, unit) if n is not None else _MISSING,
                            available=n is not None) for n in nums]
    if direction is not None:
        for i in _best_indices(nums, direction):
            cells[i].is_best = True
    return ComparisonRow(label=label, unit=unit, source=source, cells=cells, better=better)


def build_comparison(scored: list[ScoredProduct], profile: NeedProfile) -> ComparisonTable | None:
    """Bảng so sánh side-by-side top-k ứng viên. Mọi ô lấy trực tiếp từ catalog
    (giá, thông số) hoặc 'chưa có dữ liệu' — không qua LLM nên không thể bịa.
    Ô 'tốt nhất' được đánh dấu theo hướng ưu tiên (giá & mức tiêu thụ: thấp hơn tốt hơn; ...)."""
    if len(scored) < 2:
        return None  # so sánh cần tối thiểu 2 ứng viên

    products = [sp.product.display_name for sp in scored]
    rows: list[ComparisonRow] = []

    # 1) Giá — rẻ hơn được đánh dấu tốt hơn
    prices = [sp.product.price.value if sp.product.price.available else None for sp in scored]
    price_cells = [ComparisonCell(value=format_vnd(int(p)) if p is not None else _MISSING,
                                  available=p is not None) for p in prices]
    for i in _best_indices(prices, "min"):
        price_cells[i].is_best = True
    rows.append(ComparisonRow(label="Giá", unit=None, source="catalog",
                              cells=price_cells, better="giá thấp hơn tốt hơn"))

    # 2) Các tiêu chí quyết định theo nhu cầu khách (tiết kiệm điện, ít ồn, pin lâu, ...)
    for field, direction, unit in _decision_fields(profile):
        nums = [sp.product.number(field) for sp in scored]
        better = "chỉ số thấp hơn tốt hơn" if direction == "min" else "chỉ số cao hơn tốt hơn"
        rows.append(_numeric_row(field, unit, "thông số nhà sản xuất", nums, direction, better))

    # 3) Thương hiệu (tham khảo, không có "tốt nhất")
    rows.append(ComparisonRow(label="Thương hiệu", unit=None, source="catalog",
                              cells=[ComparisonCell(value=sp.product.brand) for sp in scored],
                              better=None))

    return ComparisonTable(products=products, rows=rows)
