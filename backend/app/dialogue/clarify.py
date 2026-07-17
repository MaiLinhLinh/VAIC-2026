from __future__ import annotations
from app.schemas import NeedProfile, SlotQuestion
from app.catalog.category_config import config_for, SlotSpec

MAX_QUESTIONS = 3


def _declined(profile: NeedProfile) -> bool:
    return bool(profile.constraints.get("_khong_muon_tra_loi"))


def missing_critical_slots(profile: NeedProfile, asked: list[str]) -> list[SlotSpec]:
    if profile.category is None:
        return []
    cfg = config_for(profile.category)
    out = [s for s in cfg.ask_slots
           if s.importance >= 2 and s.maps_to not in profile.constraints and s.slot not in asked]
    return sorted(out, key=lambda s: s.importance, reverse=True)


def next_question(profile: NeedProfile, asked: list[str]) -> SlotQuestion | None:
    if profile.category is None or _declined(profile) or len(asked) >= MAX_QUESTIONS:
        return None
    slots = missing_critical_slots(profile, asked)
    if not slots:
        return None
    s = slots[0]
    return SlotQuestion(slot=s.slot, text=s.question, importance=s.importance)


def should_recommend(profile: NeedProfile, asked: list[str]) -> bool:
    return profile.category is not None and next_question(profile, asked) is None


def assumptions_for(profile: NeedProfile, asked: list[str]) -> list[str]:
    notes: list[str] = []
    if profile.category is None:
        return notes
    cfg = config_for(profile.category)
    for s in cfg.ask_slots:
        if s.importance >= 2 and s.maps_to not in profile.constraints:
            notes.append(f"Em tạm bỏ qua thông tin '{s.slot}' vì mình chưa nói rõ; "
                         f"nếu cần em lọc lại chính xác hơn nhé.")
    return notes
