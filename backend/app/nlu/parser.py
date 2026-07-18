from __future__ import annotations

import re

from app.catalog.category_config import CATEGORY_CONFIGS
from app.llm.client import LLMClient
from app.nlu.preprocess import (
    canonical_constraint_key,
    declined_clarification,
    detect_category,
    expand_shorthand,
    extract_explicit_demographics,
    parse_budget_vnd,
    parse_monitor_purpose,
    parse_people_count,
    parse_screen_size_inches,
    prefers_large_screen,
    strip_accents,
)
from app.schemas import NeedProfile


_VALID_CODES = frozenset(CATEGORY_CONFIGS)
_CATEGORY_OPTIONS = ", ".join(CATEGORY_CONFIGS)
_CATEGORY_SCHEMA_VALUES = "|".join([*CATEGORY_CONFIGS, "null"])
_DEMOGRAPHIC_KEYS = {"độ tuổi", "đối tượng", "giới tính", "nghề nghiệp"}

NEED_SYSTEM_PROMPT = (
    "Bạn là bộ phân tích nhu cầu mua điện máy. Chỉ trích xuất dữ kiện khách nói rõ; "
    "thông tin thiếu phải để trống, tuyệt đối không suy đoán từ tên hay cách xưng hô. "
    f"category chỉ nhận một trong: {_CATEGORY_OPTIONS} (hoặc null nếu không rõ). "
    "budget_min/budget_max là số nguyên VND. constraints dùng khóa chuẩn: 'số người', "
    "'khối lượng', 'dung tích', 'người dùng', 'mục đích', 'kích thước', 'kiểu dáng'. "
    "prefs là các ưu tiên ngắn gọn tiếng Việt. Câu trả lời ngắn như 'gaming', '24 inch', "
    "'cho bé' vẫn phải được trích xuất; câu sửa 'không phải..., mà...' chỉ lấy giá trị mới. "
    "demographics chỉ dùng khóa 'độ tuổi', 'đối tượng', 'giới tính', 'nghề nghiệp' và phải "
    "có bằng chứng ngay trong lời khách. known liệt kê các trường đã điền."
)

NEED_SCHEMA_HINT = (
    f'{{"category": "{_CATEGORY_SCHEMA_VALUES}", "budget_min": int|null, '
    '"budget_max": int|null, "constraints": {}, "prefs": [], "demographics": {}, "known": []}'
)


def _to_profile(data: dict) -> NeedProfile:
    category = data.get("category")
    if category not in _VALID_CODES:
        category = None
    known = [field for field in data.get("known") or [] if field != "category" or category]
    return NeedProfile(
        category=category,
        budget_min=data.get("budget_min"),
        budget_max=data.get("budget_max"),
        constraints=data.get("constraints") or {},
        prefs=data.get("prefs") or [],
        demographics=data.get("demographics") or {},
        known=known,
    )


def _mark(profile: NeedProfile, field: str, present: bool) -> None:
    profile.known = [item for item in profile.known if item != field]
    if present:
        profile.known.append(field)


def _canonicalize_constraints(constraints: dict) -> dict:
    result: dict = {}
    for key, value in constraints.items():
        target = canonical_constraint_key(key) if isinstance(key, str) and not key.startswith("_") else key
        if target not in result or key == target:
            result[target] = value
    return result


def _expects_people_answer(prior: NeedProfile | None) -> bool:
    if not prior or not prior.category or "số người" in prior.constraints:
        return False
    return any(slot.kind == "people" and slot.importance >= 2
               for slot in CATEGORY_CONFIGS[prior.category].ask_slots)


def _ground_demographics(message: str, extracted: dict) -> dict[str, str]:
    """Keep arbitrary LLM values only when their literal value occurs in the message."""
    grounded = extract_explicit_demographics(message)
    flat = " ".join(strip_accents(message.lower()).split())
    for key, value in extracted.items():
        if key not in _DEMOGRAPHIC_KEYS or not isinstance(value, str):
            continue
        evidence = " ".join(strip_accents(value.lower()).split())
        if evidence and re.search(rf"\b{re.escape(evidence)}\b", flat):
            grounded.setdefault(key, value)
    return grounded


def _merge_turn(prior: NeedProfile | None, current: NeedProfile) -> NeedProfile:
    if prior is None:
        return current
    if prior.category and current.category and prior.category != current.category:
        current.demographics = {**prior.demographics, **current.demographics}
        _mark(current, "demographics", bool(current.demographics))
        current.known = list(dict.fromkeys(current.known))
        return current

    merged = prior.merge(current)
    current_purposes = {parse_monitor_purpose(pref) for pref in current.prefs}
    current_purposes.discard(None)
    if current_purposes:
        merged.prefs = [pref for pref in merged.prefs
                        if parse_monitor_purpose(pref) is None or pref in current.prefs]
    return merged


def _replace_preference(profile: NeedProfile, value: str, family) -> None:
    profile.prefs = [pref for pref in profile.prefs if not family(pref)]
    profile.prefs.append(value)
    _mark(profile, "prefs", True)


def parse_need(message: str, llm: LLMClient, prior: NeedProfile | None = None) -> NeedProfile:
    expanded = expand_shorthand(message)
    profile = _to_profile(
        llm.complete_json(NEED_SYSTEM_PROMPT, expanded, schema_hint=NEED_SCHEMA_HINT)
    )
    profile.constraints = _canonicalize_constraints(profile.constraints)

    if profile.category is None:
        profile.category = detect_category(message)
        _mark(profile, "category", profile.category is not None)

    explicit_budget = parse_budget_vnd(expanded)
    if explicit_budget != (None, None):
        profile.budget_min, profile.budget_max = explicit_budget
        _mark(profile, "budget_min", profile.budget_min is not None)
        _mark(profile, "budget_max", profile.budget_max is not None)

    # Numeric hard filters require text evidence; do not trust an unsupported model guess.
    profile.constraints.pop("số người", None)
    people = parse_people_count(message, allow_bare=_expects_people_answer(prior))
    if people:
        profile.constraints["số người"] = list(people)

    active_category = profile.category or (prior.category if prior else None)
    purpose = parse_monitor_purpose(message)
    if purpose and active_category == "man_hinh":
        _replace_preference(profile, purpose, lambda pref: parse_monitor_purpose(pref) is not None)
    if prefers_large_screen(message) and active_category in {"man_hinh", "dong_ho"}:
        _replace_preference(profile, "màn hình lớn", prefers_large_screen)

    size = parse_screen_size_inches(message)
    if size is not None and active_category in {"man_hinh", "dong_ho"}:
        profile.constraints["kích thước"] = list(size) if isinstance(size, tuple) else size

    _mark(profile, "constraints", bool(profile.constraints))
    profile.demographics = _ground_demographics(message, profile.demographics)
    _mark(profile, "demographics", bool(profile.demographics))

    if declined_clarification(message):
        profile.constraints["_khong_muon_tra_loi"] = True
        profile.known.append("_khong_muon_tra_loi")

    profile.known = list(dict.fromkeys(profile.known))
    merged = _merge_turn(prior, profile)
    # NeedProfile.merge treats None as "not supplied". Explicit bounds instead replace both sides.
    if explicit_budget != (None, None):
        merged.budget_min, merged.budget_max = explicit_budget
        _mark(merged, "budget_min", merged.budget_min is not None)
        _mark(merged, "budget_max", merged.budget_max is not None)
    return merged
