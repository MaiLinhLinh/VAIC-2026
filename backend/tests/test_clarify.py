from app.dialogue.clarify import next_question, should_recommend, missing_critical_slots, assumptions_for
from app.schemas import NeedProfile


def test_asks_critical_slot_first():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, prefs=["tiết kiệm điện"])
    q = next_question(prof, asked=[])
    assert q is not None and q.slot == "số người"     # importance 3 của tủ lạnh


def test_no_question_when_critical_filled():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000,
                       constraints={"số người": [4, 4]}, prefs=["tiết kiệm điện"])
    assert next_question(prof, asked=["kiểu dáng"]) is None
    assert should_recommend(prof, asked=["kiểu dáng"]) is True


def test_continues_from_people_to_budget_and_optional_style():
    prof = NeedProfile(category="tu_lanh")
    assert next_question(prof, asked=[]).slot == "số người"

    prof.constraints["số người"] = [4, 4]
    assert next_question(prof, asked=["số người"]).slot == "ngân sách"

    prof.budget_max = 20_000_000
    assert next_question(prof, asked=["số người", "ngân sách"]).slot == "kiểu dáng"


def test_asks_preference_after_optional_answer():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000,
                       constraints={"số người": [4, 4], "kiểu dáng": "ngăn đá dưới"})
    q = next_question(prof, asked=["số người", "kiểu dáng"])
    assert q is not None and q.slot == "ưu tiên"


def test_demographics_avoid_redundant_watch_user_question():
    prof = NeedProfile(category="dong_ho", demographics={"đối tượng": "trẻ em"})
    q = next_question(prof, asked=[])
    assert q is not None and q.slot == "ngân sách"


def test_monitor_purpose_in_preferences_is_not_asked_again():
    prof = NeedProfile(category="man_hinh", budget_max=5_000_000, prefs=["chơi game"])
    q = next_question(prof, asked=[])
    assert q is not None and q.slot == "kích thước"


def test_large_screen_preference_answers_size_question_without_exact_inches():
    prof = NeedProfile(
        category="man_hinh",
        budget_max=15_000_000,
        prefs=["chơi game", "màn hình lớn"],
    )
    assert next_question(prof, asked=["mục đích", "ngân sách", "kích thước"]) is None
    assert should_recommend(prof, asked=["mục đích", "ngân sách", "kích thước"]) is True


def test_monitor_alias_constraints_are_not_asked_again():
    prof = NeedProfile(
        category="man_hinh",
        budget_max=5_000_000,
        prefs=["chơi game"],
        constraints={"Kích thước màn hình": 24},
    )
    assert next_question(prof, asked=[]) is None


def test_follow_up_acknowledges_known_context_and_asks_one_thing():
    prof = NeedProfile(category="man_hinh", budget_max=5_000_000, prefs=["chơi game"])
    q = next_question(prof, asked=[])
    assert q is not None and q.slot == "kích thước"
    assert "chơi game" in q.text.lower()
    assert q.text.count("?") == 1


def test_budget_follow_up_accepts_rough_range_instead_of_demanding_exact_price():
    prof = NeedProfile(category="tu_lanh", constraints={"số người": [4, 4]})
    q = next_question(prof, asked=["số người"])
    assert q is not None and q.slot == "ngân sách"
    assert "khoảng" in q.text.lower() and "không cần chính xác" in q.text.lower()


def test_watch_preference_question_is_persona_aware():
    prof = NeedProfile(
        category="dong_ho",
        budget_max=2_000_000,
        demographics={"đối tượng": "trẻ em"},
    )
    q = next_question(prof, asked=["người dùng", "ngân sách"])
    assert q is not None and q.slot == "ưu tiên"
    assert "định vị" in q.text.lower()


def test_stops_after_max_questions():
    prof = NeedProfile(category="man_hinh", budget_max=20_000_000, prefs=["chơi game"], constraints={"kích thước": 27})
    assert next_question(prof, asked=["mục đích", "ngân sách", "ưu tiên"]) is None


def test_reasks_unresolved_critical_slot_even_after_max_questions():
    prof = NeedProfile(category="man_hinh", budget_max=20_000_000, prefs=["chơi game"])
    q = next_question(prof, asked=["mục đích", "ngân sách", "kích thước"])
    assert q is not None and q.slot == "kích thước"
    assert should_recommend(prof, asked=["mục đích", "ngân sách", "kích thước"]) is False


def test_retry_stays_on_latest_unresolved_slot_instead_of_jumping_back():
    prof = NeedProfile(category="man_hinh", budget_max=15_000_000)
    q = next_question(prof, asked=["mục đích", "ngân sách", "kích thước"])
    assert q is not None and q.slot == "kích thước"


def test_decline_skips_questions_and_adds_assumption():
    prof = NeedProfile(category="tu_lanh", constraints={"_khong_muon_tra_loi": True})
    assert next_question(prof, asked=[]) is None
    assert should_recommend(prof, asked=[]) is True
    assumptions = assumptions_for(prof, asked=[])
    assert any("tạm" in a.lower() for a in assumptions)


def test_assumptions_do_not_claim_missing_slot_if_slot_was_already_asked():
    prof = NeedProfile(category="man_hinh", budget_max=15_000_000, prefs=["chơi game"])
    assumptions = assumptions_for(prof, asked=["kích thước"])
    assert not any("kích thước" in a.lower() for a in assumptions)
