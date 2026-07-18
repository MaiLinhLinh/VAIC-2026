from __future__ import annotations

from typing import Iterator

from app.nlu.preprocess import strip_accents
from app.schemas import NeedProfile, Product, SourcedValue


CALL_CONSTRAINT = "thực hiện cuộc gọi"
CALL_SPEC_FIELD = "Thực hiện cuộc gọi"

# Phân bố giá trị quan sát được trong catalog.normalized.json cho các spec dạng có/không
# (ví dụ "Thực hiện cuộc gọi"): giá trị phủ định bắt đầu bằng "Không",
# giá trị chưa xác định là "Đang cập nhật" hoặc thiếu trường, còn lại là khẳng định.
_NEGATIVE_PREFIXES = ("khong",)
_UNKNOWN_MARKERS = ("dang cap nhat",)


def _norm(value: str) -> str:
    return strip_accents(value.lower()).strip()


def _keys_match(a: str, b: str) -> bool:
    return a == b or a in b or b in a


def iter_matching_specs(product: Product, key: str) -> Iterator[tuple[str, SourcedValue]]:
    """Yield specs whose (accent-insensitive) name matches the constraint key."""
    norm_key = _norm(key)
    for spec_key, sv in product.specs.items():
        if _keys_match(norm_key, _norm(spec_key)):
            yield spec_key, sv


def capability_status(product: Product, key: str) -> str | None:
    """Text value of the spec matching `key`, falling back to raw catalog data."""
    for _, sv in iter_matching_specs(product, key):
        if sv.available and isinstance(sv.value, str) and sv.value.strip():
            return sv.value.strip()
    norm_key = _norm(key)
    for raw_key, raw_val in product.raw.items():
        if _keys_match(norm_key, _norm(raw_key)) and isinstance(raw_val, str) and raw_val.strip():
            return raw_val.strip()
    return None


def is_affirmative(status: str) -> bool:
    norm = _norm(status)
    if norm.startswith(_NEGATIVE_PREFIXES):
        return False
    return not any(marker in norm for marker in _UNKNOWN_MARKERS)


def supports_capability(product: Product, key: str) -> bool:
    """Missing, negative, or not-yet-updated data fails closed; affirmative values pass."""
    status = capability_status(product, key)
    return status is not None and is_affirmative(status)


def requires_call(profile: NeedProfile) -> bool:
    return profile.constraints.get(CALL_CONSTRAINT) is True


def call_status(product: Product) -> str | None:
    return capability_status(product, CALL_SPEC_FIELD)


def product_supports_call(product: Product) -> bool:
    return supports_capability(product, CALL_SPEC_FIELD)
