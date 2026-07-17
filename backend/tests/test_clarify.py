from app.dialogue.clarify import next_question, should_recommend, missing_critical_slots, assumptions_for
from app.schemas import NeedProfile


def test_asks_critical_slot_first():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, prefs=["tiết kiệm điện"])
    q = next_question(prof, asked=[])
    assert q is not None and q.slot == "số người"     # importance 3 của tủ lạnh


def test_no_question_when_critical_filled():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, constraints={"số người": [3, 4]})
    assert next_question(prof, asked=["kiểu dáng"]) is None
    assert should_recommend(prof, asked=["kiểu dáng"]) is True


def test_stops_after_max_questions():
    prof = NeedProfile(category="man_hinh")
    assert next_question(prof, asked=["mục đích", "kích thước", "x"]) is None


def test_decline_skips_questions_and_adds_assumption():
    prof = NeedProfile(category="tu_lanh", constraints={"_khong_muon_tra_loi": True})
    assert next_question(prof, asked=[]) is None
    assert should_recommend(prof, asked=[]) is True
    assumptions = assumptions_for(prof, asked=[])
    assert any("tạm" in a.lower() for a in assumptions)
