from __future__ import annotations
import math
import re
from app.schemas import SourcedValue

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
_NEG_WORDS = {"không", "không có", "không cảm ứng", "n/a", "na", "-", ""}


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def _clean(s) -> str | None:
    if s is None or _is_nan(s):
        return None
    text = str(s).strip()
    if text.lower() in _NEG_WORDS:
        return None
    return text


def _to_float(tok: str) -> float:
    return float(tok.replace(",", "."))


def parse_number(s) -> float | None:
    if isinstance(s, (int, float)) and not _is_nan(s):
        return float(s)
    text = _clean(s)
    if text is None:
        return None
    m = _NUM.search(text)
    return _to_float(m.group(0)) if m else None


def parse_range(s) -> tuple[float, float] | None:
    if isinstance(s, (int, float)) and not _is_nan(s):
        return (float(s), float(s))
    text = _clean(s)
    if text is None:
        return None
    nums = _NUM.findall(text)
    if not nums:
        return None
    if len(nums) == 1:
        v = _to_float(nums[0])
        return (v, v)
    lo, hi = _to_float(nums[0]), _to_float(nums[1])
    return (min(lo, hi), max(lo, hi))


def parse_measure(s) -> float | None:
    r = parse_range(s)
    if r is None:
        return None
    lo, hi = r
    return (lo + hi) / 2 if lo != hi else lo


def parse_bool(s) -> bool | None:
    if _is_nan(s) or s is None:
        return None
    text = str(s).strip().lower()
    if text == "":
        return None
    if text.startswith("không"):
        return False
    if text in {"có", "co"}:
        return True
    return None


def parse_people(s) -> tuple[int, int] | None:
    r = parse_range(s)
    if r is None:
        return None
    return (int(r[0]), int(r[1]))


def resolve_price(gia_goc, gia_km):
    orig_n = parse_number(gia_goc)
    sale_n = parse_number(gia_km)
    orig = SourcedValue.of(int(orig_n), "catalog") if orig_n else SourcedValue.missing()
    sale_valid = sale_n is not None and sale_n > 0 and (orig_n is None or sale_n <= orig_n)
    sale = SourcedValue.of(int(sale_n), "catalog") if sale_valid else SourcedValue.missing()
    if sale_valid:
        price = SourcedValue.of(int(sale_n), "catalog", detail="giá khuyến mãi")
    elif orig_n:
        price = SourcedValue.of(int(orig_n), "catalog", detail="giá gốc")
    else:
        price = SourcedValue.missing()
    return price, orig, sale
