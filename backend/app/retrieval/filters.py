from __future__ import annotations
from app.schemas import Product, NeedProfile

_PEOPLE_FIELD = "Số người sử dụng"


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
    lowered = key.lower()
    for spec_key in p.specs:
        spec_lower = spec_key.lower()
        if lowered == spec_lower or lowered in spec_lower or spec_lower in lowered:
            num = p.number(spec_key)
            if num is not None:
                return num
    return None


def _passes_constraints(p: Product, profile: NeedProfile) -> bool:
    for key, val in profile.constraints.items():
        if key.startswith("_"):
            continue
        if key == "số người" and isinstance(val, list) and len(val) == 2:
            sv = p.specs.get(_PEOPLE_FIELD)
            if sv and sv.available and isinstance(sv.value, list):
                lo, hi = sv.value
                if hi < val[0] or lo > val[1]:   # không giao nhau
                    return False
        elif isinstance(val, list) and len(val) == 2:
            num = _numeric_spec_value(p, key)
            if num is not None:
                lo, hi = val
                if lo is not None and num < lo:
                    return False
                if hi is not None and num > hi:
                    return False
        elif isinstance(val, (int, float)):
            num = _numeric_spec_value(p, key)
            if num is not None and not (val * 0.75 <= num <= val * 1.25):
                return False
    return True


def apply_hard_filters(products: list[Product], profile: NeedProfile) -> list[Product]:
    return [p for p in products if _passes_budget(p, profile) and _passes_constraints(p, profile)]
