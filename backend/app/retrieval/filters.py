from __future__ import annotations
from app.schemas import Product, NeedProfile
from app.catalog.capabilities import iter_matching_specs, supports_capability


def count_no_price(products: list[Product]) -> int:
    return sum(1 for p in products if not p.price.available)


def _passes_budget(p: Product, profile: NeedProfile) -> bool:
    if not p.price.available:
        return False
    v = p.price.value
    if profile.budget_max is not None and v > profile.budget_max:
        return False
    if profile.budget_min is not None and v < profile.budget_min:
        return False
    return True


def _numeric_spec_value(p: Product, key: str) -> float | None:
    for _, sv in iter_matching_specs(p, key):
        if sv.available and isinstance(sv.value, (int, float)):
            return sv.value
    return None


def _passes_range(p: Product, key: str, bounds: list) -> bool:
    lo, hi = bounds
    for _, sv in iter_matching_specs(p, key):
        if not sv.available:
            continue
        v = sv.value
        if isinstance(v, list) and len(v) == 2:
            # spec là một khoảng (vd "Số người sử dụng"): yêu cầu giao nhau với khoảng cần
            s_lo, s_hi = v
            if hi is not None and s_lo > hi:
                return False
            if lo is not None and s_hi < lo:
                return False
            return True
        if isinstance(v, (int, float)):
            if lo is not None and v < lo:
                return False
            if hi is not None and v > hi:
                return False
            return True
    return True  # thiếu dữ liệu: không loại ở bước lọc cứng


def _passes_constraints(p: Product, profile: NeedProfile) -> bool:
    for key, val in profile.constraints.items():
        if key.startswith("_"):
            continue
        if val is True:
            if not supports_capability(p, key):
                return False
        elif isinstance(val, list) and len(val) == 2:
            if not _passes_range(p, key, val):
                return False
        elif isinstance(val, (int, float)):
            num = _numeric_spec_value(p, key)
            if num is not None and not (val * 0.75 <= num <= val * 1.25):
                return False
    return True


def apply_hard_filters(products: list[Product], profile: NeedProfile) -> list[Product]:
    return [p for p in products if _passes_budget(p, profile) and _passes_constraints(p, profile)]
