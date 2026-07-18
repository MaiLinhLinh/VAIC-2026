from __future__ import annotations

from app.catalog.category_config import SlotSpec, config_for
from app.nlu.preprocess import canonical_constraint_key, parse_monitor_purpose
from app.schemas import NeedProfile, SlotQuestion


MAX_QUESTIONS = 3
_BUDGET_SLOT = SlotSpec(
    "ngân sách",
    "Anh/chị dự kiến ngân sách khoảng bao nhiêu để em lọc đúng tầm giá ạ?",
    3,
    "ngân sách",
    "number",
)


def _declined(profile: NeedProfile) -> bool:
    return bool(profile.constraints.get("_khong_muon_tra_loi"))


def _slot_filled(profile: NeedProfile, slot: SlotSpec) -> bool:
    if slot.slot == "ngân sách":
        return profile.budget_min is not None or profile.budget_max is not None
    if slot.slot == "ưu tiên":
        return bool(profile.prefs)
    if any(canonical_constraint_key(key) == slot.maps_to
           for key in profile.constraints if isinstance(key, str)):
        return True
    if slot.slot == "kích thước":
        return "màn hình lớn" in profile.prefs
    if slot.slot == "người dùng":
        return bool(profile.demographics)
    if slot.slot == "mục đích":
        return any(parse_monitor_purpose(pref) for pref in profile.prefs)
    return False


def _category_slots(profile: NeedProfile, *, critical: bool) -> list[SlotSpec]:
    if profile.category is None:
        return []
    slots = [slot for slot in config_for(profile.category).ask_slots
             if (slot.importance >= 2) == critical]
    if critical:
        slots.append(_BUDGET_SLOT)
    return sorted(slots, key=lambda slot: slot.importance, reverse=True)


def _unfilled_slots(profile: NeedProfile, *, critical: bool, asked: set[str]) -> list[SlotSpec]:
    return [slot for slot in _category_slots(profile, critical=critical)
            if slot.slot not in asked and not _slot_filled(profile, slot)]


def missing_critical_slots(profile: NeedProfile, asked: list[str]) -> list[SlotSpec]:
    return _unfilled_slots(profile, critical=True, asked=set(asked))


def unresolved_critical_slots(profile: NeedProfile) -> list[SlotSpec]:
    return _unfilled_slots(profile, critical=True, asked=set())


def _preference_question(profile: NeedProfile) -> str:
    audience = profile.demographics.get("đối tượng")
    if profile.category == "dong_ho" and audience == "trẻ em":
        choices = "định vị, liên lạc hay theo dõi vận động"
    elif profile.category == "dong_ho" and audience == "người cao tuổi":
        choices = "dễ đọc, theo dõi sức khỏe hay pin lâu"
    else:
        choices = ", ".join(config_for(profile.category).pref_lexicon)
    return f"Anh/chị ưu tiên điều gì nhất: {choices} ạ?"


def _optional_slots(profile: NeedProfile, asked: list[str]) -> list[SlotSpec]:
    slots = _unfilled_slots(profile, critical=False, asked=set(asked))
    if not profile.prefs and "ưu tiên" not in asked:
        slots.append(SlotSpec("ưu tiên", _preference_question(profile), 1, "prefs", "text"))
    return slots


def _known_context(profile: NeedProfile) -> str | None:
    people = profile.constraints.get("số người")
    if isinstance(people, list) and len(people) == 2 and people[0] == people[1]:
        return f"nhà mình {people[0]} người"
    if profile.category == "man_hinh":
        purpose = next((pref for pref in profile.prefs if parse_monitor_purpose(pref)), None)
        return f"nhu cầu {purpose}" if purpose else None
    audience = profile.demographics.get("đối tượng")
    return f"đồng hồ cho {audience}" if profile.category == "dong_ho" and audience else None


def _question(profile: NeedProfile, slot: SlotSpec) -> SlotQuestion:
    context = _known_context(profile)
    if slot.slot == "ngân sách":
        prefix = f"Với {context}, " if context else ""
        text = (f"{prefix}anh/chị dự kiến ngân sách khoảng bao nhiêu "
                "(không cần chính xác, mình có thể cho em một khoảng) ạ?")
    elif slot.slot == "kích thước" and context:
        text = f"Với {context}, anh/chị muốn màn khoảng bao nhiêu inch ạ?"
    else:
        text = slot.question
    return SlotQuestion(slot=slot.slot, text=text, importance=slot.importance)


def next_question(profile: NeedProfile, asked: list[str]) -> SlotQuestion | None:
    if profile.category is None or _declined(profile):
        return None

    if len(asked) >= MAX_QUESTIONS:
        unresolved = {slot.slot: slot for slot in unresolved_critical_slots(profile)}
        slot = next((unresolved[name] for name in reversed(asked) if name in unresolved), None)
        return _question(profile, slot) if slot else None

    slots = missing_critical_slots(profile, asked)
    if not slots and asked and not profile.prefs:
        slots = _optional_slots(profile, asked)
    return _question(profile, slots[0]) if slots else None


def should_recommend(profile: NeedProfile, asked: list[str]) -> bool:
    return bool(
        profile.category
        and (_declined(profile)
             or (not unresolved_critical_slots(profile) and next_question(profile, asked) is None))
    )


def assumptions_for(profile: NeedProfile, asked: list[str]) -> list[str]:
    return [
        f"Em tạm bỏ qua thông tin '{slot.slot}' vì mình chưa nói rõ; "
        "nếu cần em lọc lại chính xác hơn nhé."
        for slot in missing_critical_slots(profile, asked)
    ]
