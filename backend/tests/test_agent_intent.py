from app.llm.client import FakeLLM
from app.agent_core.intent import extract_intent, extract_intent_fallback, has_enough_slots
from app.agent_core.slots import merge_slot_updates
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


def test_intent_reuses_same_call_for_natural_followup_and_slot_updates(tmp_path):
    db = _db(tmp_path)
    llm = FakeLLM(json_responses=[{
        "category": "Tủ Lạnh",
        "has_usage_context": True,
        "slot_updates": [{
            "name": "Dung tích tổng", "value": "khoảng 300 lít",
            "status": "filled", "basis": "stated", "hard": False,
        }],
        "followup_focus": "budget",
        "followup_fields": [],
        "followup_question": (
            "Dạ, tủ khoảng 300 lít sẽ vừa nhu cầu gia đình mình. "
            "Anh/chị muốn cân đối ngân sách trong khoảng nào ạ?"
        ),
    }])
    context = {
        "category": "Tủ Lạnh",
        "expected_focus_before_current_answer": "usage",
        "spec_columns": ["Dung tích tổng", "Điện năng tiêu thụ"],
        "current_slots": [],
    }

    intent = extract_intent(
        "nhà 4 người, tôi muốn loại khoảng 300 lít", [], llm, db,
        discovery_context=context,
    )

    assert len(llm.calls) == 1
    assert intent["followup_focus"] == "budget"
    assert "300 lít" in intent["followup_question"]
    assert intent["slot_updates"][0]["name"] == "Dung tích tổng"
    assert '"spec_columns": ["Dung tích tổng", "Điện năng tiêu thụ"]' in llm.calls[0][1]


def test_merge_slot_updates_rejects_invented_columns_and_preserves_hard_constraint():
    current = [{
        "name": "Loại sản phẩm", "value": "laser", "status": "filled",
        "basis": "stated", "hard": True,
    }]
    updates = [
        {"name": "Loại sản phẩm", "value": "laser màu", "status": "filled",
         "basis": "stated", "hard": False},
        {"name": "Kết nối", "value": "Wi-Fi", "status": "filled",
         "basis": "stated", "hard": False},
        {"name": "Độ tuổi", "value": "30", "status": "filled",
         "basis": "stated", "hard": False},
    ]

    slots = merge_slot_updates(["Loại sản phẩm", "Kết nối"], current, updates)
    by_name = {slot["name"]: slot for slot in slots}

    assert set(by_name) == {"Loại sản phẩm", "Kết nối"}
    assert by_name["Loại sản phẩm"]["hard"] is True


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


def test_code_request_is_off_topic_not_unsupported_product(tmp_path):
    db = _db(tmp_path)
    llm = FakeLLM(json_responses=[{
        "category": None, "unsupported_product": "code C++",
        "is_chitchat": False, "is_policy_question": False,
        "needs_clarification": False,
    }])
    intent = extract_intent("hãy code cho tôi file C++ ra dòng Hello World", [], llm, db)
    assert intent["is_chitchat"] is True
    assert intent["unsupported_product"] is None
    assert intent["category"] is None


def test_intent_prompt_requires_short_general_knowledge_reply(tmp_path):
    db = _db(tmp_path)
    llm = FakeLLM(json_responses=[{
        "is_chitchat": True,
        "smalltalk_reply": "Kinh tế chính trị nghiên cứu kinh tế trong quan hệ với quyền lực xã hội.",
    }])
    intent = extract_intent("kinh tế chính trị là gì", [], llm, db)
    assert intent["is_chitchat"] is True
    assert intent["smalltalk_reply"]
    assert "BẮT BUỘC điền smalltalk_reply" in llm.calls[0][0]


def test_shopping_for_computer_to_code_is_not_off_topic(tmp_path):
    db = str(tmp_path / "shop-code.db")
    make_db(db, [{"category": "Máy tính để bàn", "brand": "Dell",
                  "price_clean": 15_000_000, "specs": {}}])
    llm = FakeLLM(json_responses=[{
        "category": "Máy tính để bàn", "is_chitchat": False,
        "unsupported_product": None,
        "needs_clarification": False,
    }])
    intent = extract_intent("tôi muốn mua máy tính để bàn để code", [], llm, db)
    assert intent["is_chitchat"] is False
    assert intent["category"] == "Máy tính để bàn"
