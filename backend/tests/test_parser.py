from app.llm.client import FakeLLM
from app.nlu.parser import parse_need
from app.schemas import NeedProfile


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
    assert np_.constraints == {"số người": [3, 4]}


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
