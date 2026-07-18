import pytest

from app.catalog.category_config import CATEGORY_CONFIGS
from app.llm.client import FakeLLM
from app.nlu.parser import NEED_SCHEMA_HINT, NEED_SYSTEM_PROMPT, parse_need
from app.schemas import NeedProfile


def test_need_prompt_and_schema_list_every_configured_category():
    prompt_options = ", ".join(CATEGORY_CONFIGS)
    schema_options = "|".join([*CATEGORY_CONFIGS, "null"])

    assert f"category chỉ nhận một trong: {prompt_options}" in NEED_SYSTEM_PROMPT
    assert f'"category": "{schema_options}"' in NEED_SCHEMA_HINT


@pytest.mark.parametrize("category", CATEGORY_CONFIGS)
def test_parse_need_accepts_every_configured_category(category):
    fake = FakeLLM(json_responses=[{
        "category": category,
        "constraints": {},
        "prefs": [],
        "known": ["category"],
    }])

    profile = parse_need("", fake)

    assert profile.category == category
    assert "category" in profile.known


def test_parse_need_maps_llm_json():
    fake = FakeLLM(json_responses=[{
        "category": "tu_lanh", "budget_max": 20000000, "budget_min": None,
        "constraints": {"số người": [3, 4]}, "prefs": ["tiết kiệm điện", "ít ồn"],
        "demographics": {}, "known": ["category", "budget_max", "constraints", "prefs"],
    }])
    np_ = parse_need("mua tu lanh duoi 20tr cho gia dinh 4 nguoi, tiet kiem dien, it on", fake)
    assert np_.category == "tu_lanh"
    assert np_.budget_max == 20_000_000
    assert np_.prefs == ["tiết kiệm điện", "ít ồn"]
    assert "category" in np_.known


def test_parse_need_deterministic_fallback_when_llm_misses_category():
    fake = FakeLLM(json_responses=[{"category": None, "prefs": [], "constraints": {}, "known": []}])
    np_ = parse_need("cho minh cai man hinh gaming duoi 5 trieu", fake)
    assert np_.category == "man_hinh"       # detect_category bù
    assert np_.budget_max == 5_000_000       # parse_budget_vnd bù
    assert "category" in np_.known and "budget_max" in np_.known


def test_parse_need_merges_prior_keeping_known():
    prior = NeedProfile(category="tu_lanh", budget_max=20_000_000, known=["category", "budget_max"])
    fake = FakeLLM(json_responses=[{"category": None, "constraints": {"số người": [3, 4]},
                                    "prefs": [], "known": ["constraints"]}])
    np_ = parse_need("nhà mình 4 người", fake, prior=prior)
    assert np_.category == "tu_lanh" and np_.budget_max == 20_000_000
    assert np_.constraints == {"số người": [4, 4]}


def test_short_people_answer_uses_prior_question_context():
    prior = NeedProfile(category="tu_lanh", known=["category"])
    fake = FakeLLM(json_responses=[{
        "category": None, "constraints": {}, "prefs": [], "known": [],
    }])
    np_ = parse_need("4", fake, prior=prior)
    assert np_.category == "tu_lanh"
    assert np_.constraints["số người"] == [4, 4]


def test_invalid_llm_category_scrubbed_from_known():
    fake = FakeLLM(json_responses=[{"category": "Tủ Lạnh", "prefs": [],
                                    "constraints": {}, "known": ["category"]}])
    np_ = parse_need("xin chào em", fake)   # invalid code + message has no detectable category
    assert np_.category is None
    assert "category" not in np_.known


def test_decline_phrase_sets_flag():
    fake = FakeLLM(json_responses=[{"category": "tu_lanh", "prefs": [], "constraints": {}, "known": ["category"]}])
    np_ = parse_need("tu lanh gia re cu goi y dai di em", fake)
    assert np_.constraints.get("_khong_muon_tra_loi") is True


def test_people_fallback_overrides_ambiguous_llm_value():
    fake = FakeLLM(json_responses=[{
        "category": "tu_lanh", "prefs": [],
        "constraints": {"số người": [3, 4]}, "known": ["category", "constraints"],
    }])
    np_ = parse_need("mua tủ lạnh cho gia đình 4 người", fake)
    assert np_.constraints["số người"] == [4, 4]
    assert "constraints" in np_.known


def test_people_fallback_handles_no_accents_when_llm_misses():
    fake = FakeLLM(json_responses=[{
        "category": "tu_lanh", "prefs": [], "constraints": {}, "known": ["category"],
    }])
    np_ = parse_need("nha bon nguoi can tu lanh", fake)
    assert np_.constraints["số người"] == [4, 4]


def test_explicit_demographics_replace_unsupported_llm_guess():
    fake = FakeLLM(json_responses=[{
        "category": "dong_ho", "prefs": [], "constraints": {},
        "demographics": {"giới tính": "nam", "nghề nghiệp": "giám đốc"},
        "known": ["category", "demographics"],
    }])
    np_ = parse_need("cần đồng hồ thông minh pin lâu", fake)
    assert np_.demographics == {}
    assert "demographics" not in np_.known


def test_extracts_demographics_only_from_clear_context():
    fake = FakeLLM(json_responses=[{
        "category": "dong_ho", "prefs": [], "constraints": {},
        "demographics": {}, "known": ["category"],
    }])
    np_ = parse_need("tôi là nữ, 30 tuổi, làm giáo viên và cần đồng hồ", fake)
    assert np_.demographics == {
        "độ tuổi": "30 tuổi",
        "giới tính": "nữ",
        "nghề nghiệp": "giáo viên",
    }
    assert "demographics" in np_.known


def test_constraint_aliases_are_canonicalized_to_avoid_reasking():
    fake = FakeLLM(json_responses=[{
        "category": "man_hinh", "prefs": ["chơi game"],
        "constraints": {"Kích thước màn hình": 24},
        "known": ["category", "constraints", "prefs"],
    }])

    np_ = parse_need("màn hình gaming 24 inch", fake)

    assert np_.constraints == {"kích thước": 24}


def test_natural_decline_stops_further_clarification():
    prior = NeedProfile(category="tu_lanh", known=["category"])
    fake = FakeLLM(json_responses=[{
        "category": None, "constraints": {}, "prefs": [], "known": [],
    }])

    np_ = parse_need("mình không biết nữa, cứ gợi ý giúp nhé", fake, prior=prior)

    assert np_.constraints["_khong_muon_tra_loi"] is True


def test_switching_product_category_does_not_leak_old_product_needs():
    prior = NeedProfile(
        category="tu_lanh",
        budget_max=20_000_000,
        constraints={"số người": [4, 4], "_khong_muon_tra_loi": True},
        prefs=["tiết kiệm điện"],
        demographics={"nghề nghiệp": "giáo viên"},
        known=["category", "budget_max", "constraints", "prefs", "demographics"],
    )
    fake = FakeLLM(json_responses=[{
        "category": "man_hinh", "budget_max": 3_990_000,
        "constraints": {"Kích thước màn hình": 24},
        "prefs": ["chơi game"],
        "demographics": {},
        "known": ["category", "budget_max", "constraints", "prefs"],
    }])

    np_ = parse_need("mình mua màn hình gaming 24 inch loại 3990k", fake, prior=prior)

    assert np_.category == "man_hinh"
    assert np_.budget_max == 3_990_000
    assert np_.constraints == {"kích thước": 24}
    assert np_.prefs == ["chơi game"]
    assert np_.demographics == {"nghề nghiệp": "giáo viên"}


def test_explicit_budget_language_overrides_wrong_llm_direction():
    fake = FakeLLM(json_responses=[{
        "category": "may_rua_chen",
        "budget_min": 30_000_000,
        "budget_max": None,
        "constraints": {},
        "prefs": [],
        "known": ["category", "budget_min"],
    }])

    np_ = parse_need("khoảng 30tr quay đầu", fake)

    assert np_.budget_min is None
    assert np_.budget_max == 30_000_000


def test_explicit_budget_replaces_both_bounds_from_prior_turn():
    prior = NeedProfile(category="man_hinh", budget_min=10_000_000)
    fake = FakeLLM(json_responses=[{
        "category": None, "budget_min": None, "budget_max": 8_000_000,
        "constraints": {}, "prefs": [], "known": ["budget_max"],
    }])

    profile = parse_need("đổi lại dưới 8tr", fake, prior=prior)

    assert profile.budget_min is None
    assert profile.budget_max == 8_000_000


def test_arbitrary_llm_demographic_is_kept_when_grounded_in_message():
    fake = FakeLLM(json_responses=[{
        "category": "man_hinh", "constraints": {}, "prefs": [],
        "demographics": {"nghề nghiệp": "kiến trúc sư"},
        "known": ["category", "demographics"],
    }])

    profile = parse_need("tôi là kiến trúc sư, cần mua màn hình", fake)

    assert profile.demographics == {"nghề nghiệp": "kiến trúc sư"}


def test_cu_budget_is_a_maximum_even_when_llm_emits_exact_interval():
    fake = FakeLLM(json_responses=[{
        "category": None,
        "budget_min": 15_000_000,
        "budget_max": 15_000_000,
        "constraints": {},
        "prefs": [],
        "known": ["budget_min", "budget_max"],
    }])

    np_ = parse_need("15 củ", fake, prior=NeedProfile(category="man_hinh"))

    assert np_.budget_min is None
    assert np_.budget_max == 15_000_000


def test_explicit_screen_size_minimum_is_parsed_deterministically():
    prior = NeedProfile(category="man_hinh", budget_max=15_000_000, prefs=["chơi game"])
    fake = FakeLLM(json_responses=[{
        "category": None,
        "constraints": {},
        "prefs": [],
        "known": [],
    }])

    np_ = parse_need("vậy tối thiểu 15 inch", fake, prior=prior)

    assert np_.category == "man_hinh"
    assert np_.constraints["kích thước"] == [15.0, None]


def test_short_game_answer_is_normalized_without_relying_on_llm():
    prior = NeedProfile(category="man_hinh", known=["category"])
    fake = FakeLLM(json_responses=[{
        "category": None,
        "constraints": {},
        "prefs": [],
        "known": [],
    }])

    np_ = parse_need("game", fake, prior=prior)

    assert np_.prefs == ["chơi game"]
    assert "prefs" in np_.known


def test_relative_large_screen_answer_becomes_soft_preference():
    prior = NeedProfile(
        category="man_hinh",
        budget_max=15_000_000,
        prefs=["chơi game"],
    )
    fake = FakeLLM(json_responses=[{
        "category": None,
        "constraints": {},
        "prefs": [],
        "known": [],
    }])

    np_ = parse_need("càng to càng tốt", fake, prior=prior)

    assert np_.prefs == ["chơi game", "màn hình lớn"]
    assert "kích thước" not in np_.constraints


def test_relative_low_price_answer_becomes_soft_preference():
    prior = NeedProfile(category="tu_lanh", constraints={"số người": [5, 5]})
    fake = FakeLLM(json_responses=[{
        "category": None,
        "constraints": {},
        "prefs": [],
        "known": [],
    }])

    np_ = parse_need("càng rẻ càng tốt", fake, prior=prior)

    assert np_.prefs == ["giá thấp"]
    assert np_.budget_min is None
    assert np_.budget_max is None


def test_watch_calling_is_a_hard_grounded_constraint():
    prior = NeedProfile(category="dong_ho", demographics={"đối tượng": "trẻ em"})
    fake = FakeLLM(json_responses=[{
        "category": None,
        "constraints": {},
        "prefs": [],
        "known": [],
    }])

    np_ = parse_need("1 triệu thôi, nghe gọi được", fake, prior=prior)

    assert np_.budget_max == 1_000_000
    assert np_.constraints["thực hiện cuộc gọi"] is True
