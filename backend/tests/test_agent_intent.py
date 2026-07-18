from app.llm.client import FakeLLM
from app.agent_core.intent import extract_intent, extract_intent_fallback, has_enough_slots
from tests.agent_helpers import make_db


class BoomLLM:
    def complete_json(self, system, user, schema_hint=""):
        raise RuntimeError("llm down")

    def complete_text(self, s, u):
        return ""

    def stream_text(self, s, u):
        yield ""


def _db(tmp_path):
    db = str(tmp_path / "i.db")
    make_db(db, [{"category": "Tủ Lạnh", "brand": "Toshiba", "price_clean": 12_000_000, "specs": {}},
                 {"category": "Máy giặt", "brand": "LG", "price_clean": 9_000_000, "specs": {}}])
    return db


def test_llm_intent_maps_fields(tmp_path):
    db = _db(tmp_path)
    llm = FakeLLM(json_responses=[{"category": "Tủ Lạnh", "budget_max": 20000000,
                                   "brand": "Toshiba", "priority_features": ["tiết kiệm điện"],
                                   "needs_clarification": False, "is_meta_inquiry": False,
                                   "clarification_questions": []}])
    intent = extract_intent("mua tủ lạnh toshiba dưới 20tr tiết kiệm điện", [], llm, db)
    assert intent["category"] == "Tủ Lạnh"
    assert intent["budget_max"] == 20000000
    assert intent["priority_features"] == ["tiết kiệm điện"]
    assert intent["needs_clarification"] is False


def test_llm_error_falls_back(tmp_path):
    db = _db(tmp_path)
    intent = extract_intent("mua tủ lạnh 15 triệu", [], BoomLLM(), db)
    assert intent["category"] == "Tủ Lạnh"
    assert intent["budget_max"] == 15_000_000


def test_fallback_detects_budget_and_brand(tmp_path):
    db = _db(tmp_path)
    intent = extract_intent_fallback("máy giặt LG khoảng 9 triệu", [], db)
    assert intent["category"] == "Máy giặt"
    assert intent["brand"] == "LG"


def test_has_enough_slots():
    assert has_enough_slots({"category": "Tủ Lạnh", "budget_max": 20000000,
                             "priority_features": [], "needs_clarification": False}) is True
    assert has_enough_slots({"category": None, "budget_max": None, "brand": None,
                             "priority_features": [], "needs_clarification": True}) is False
