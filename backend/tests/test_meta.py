import pytest

from app.catalog.loader import ProductStore
from app.dialogue.clarify import next_question
from app.dialogue.meta import detect_meta_intent, options_reply
from app.llm.client import FakeLLM
from app.orchestrator import ChatState, Orchestrator
from app.schemas import NeedProfile, Product, SourcedValue


def mk(brand, price, dien):
    return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs={"Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất", unit="kWh/năm"),
                          "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất"),
                          "Công nghệ tiết kiệm điện": SourcedValue.of("Inverter", "thông số nhà sản xuất")},
                   spec_doc="inverter", promo_text=None, raw={})


def store():
    return ProductStore([mk("A", 12_000_000, 300), mk("B", 11_000_000, 400), mk("C", 9_000_000, 380)])


# ---------- detect_meta_intent ----------

@pytest.mark.parametrize("message,expected", [
    ("xin chào", "greeting"),
    ("chào em nhé", "greeting"),
    ("cảm ơn em nhiều nhé", "thanks"),
    ("ok cảm ơn", "thanks"),
    ("bạn làm được gì?", "capability"),
    ("em tư vấn được những gì", "capability"),
    ("shop bán những sản phẩm nào", "capability"),
])
def test_detects_meta_intents_without_pending_question(message, expected):
    assert detect_meta_intent(message, stage="collecting", pending_slot=None) == expected


@pytest.mark.parametrize("message,expected", [
    ("tôi không biết", "dont_know"),
    ("chưa rõ nữa em ơi", "dont_know"),
    ("bỏ qua đi", "dont_know"),
    ("tùy", "dont_know"),
    ("có những option nào?", "ask_options"),
    ("có những mức giá nào vậy em", "ask_options"),
    ("thường mọi người chọn gì", "ask_options"),
    ("tầm bao nhiêu là hợp lý", "ask_options"),
])
def test_detects_meta_intents_with_pending_question(message, expected):
    assert detect_meta_intent(message, stage="collecting", pending_slot="ngân sách") == expected


@pytest.mark.parametrize("message", [
    "xin chào, tôi muốn mua tủ lạnh",          # có nội dung thật -> vào parser
    "không biết, chắc tầm 5 triệu",             # kèm dữ kiện ngân sách -> vào parser
    "tuy nhiên tôi thích inverter",             # "tuy nhiên" không phải "tùy"
    "nhà 4 người",
])
def test_content_messages_are_not_meta(message):
    assert detect_meta_intent(message, stage="collecting", pending_slot="ngân sách") is None


def test_dont_know_ignored_when_no_pending_question():
    assert detect_meta_intent("tôi không biết", stage="recommended", pending_slot=None) is None


# ---------- options_reply ----------

def test_options_reply_for_budget_uses_catalog_price_range():
    profile = NeedProfile(category="tu_lanh")
    reply = options_reply(profile, "ngân sách", store())
    assert "9.000.000đ" in reply and "12.000.000đ" in reply


def test_options_reply_for_preferences_lists_lexicon():
    profile = NeedProfile(category="tu_lanh")
    reply = options_reply(profile, "ưu tiên", store())
    assert "tiết kiệm điện" in reply


def test_options_reply_for_style_slot_gives_choices():
    profile = NeedProfile(category="tu_lanh")
    reply = options_reply(profile, "kiểu dáng", store())
    assert "ngăn đá" in reply


# ---------- clarify: skipped slots ----------

def test_skipped_slot_not_reasked_after_max_questions():
    profile = NeedProfile(category="tu_lanh", constraints={"số người": [4, 4]},
                          prefs=["tiết kiệm điện"])
    asked = ["số người", "kiểu dáng", "ngân sách"]
    # không skip -> vòng hỏi lại đòi ngân sách; skip -> dừng hỏi
    assert next_question(profile, asked) is not None
    assert next_question(profile, asked, skipped=["ngân sách"]) is None


# ---------- orchestrator flows ----------

def _first_turn(orch):
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    return orch.handle_turn(
        state, "tủ lạnh cho nhà 4 người, tiết kiệm điện")


def _llm():
    return FakeLLM(
        json_responses=[{"category": "tu_lanh", "constraints": {"số người": [4, 4]},
                         "prefs": ["tiết kiệm điện"],
                         "known": ["category", "constraints", "prefs"]}],
        text_responses=["Em gợi ý các máy có giá 12.000.000đ, 11.000.000đ và 9.000.000đ."])


def test_dont_know_skips_budget_and_recommends_instead_of_repeating():
    orch = Orchestrator(store(), _llm())
    state, res = _first_turn(orch)
    assert res.stage == "collecting" and "ngân sách" in (res.question or "")

    state, res2 = orch.handle_turn(state, "tôi không biết nữa")
    assert res2.stage == "recommended"
    assert res2.advice is not None and res2.advice.cards
    assert "ngân sách" in state.skipped
    # câu hỏi cũ không được lặp lại
    assert "ngân sách khoảng bao nhiêu" not in res2.reply


def test_ask_options_on_budget_question_answers_with_price_range():
    orch = Orchestrator(store(), _llm())
    state, res = _first_turn(orch)
    assert "ngân sách" in (res.question or "")

    state, res2 = orch.handle_turn(state, "có những mức giá nào?")
    assert res2.stage == "collecting"
    assert "9.000.000đ" in res2.reply and "12.000.000đ" in res2.reply
    # state không đổi: khách vẫn có thể trả lời câu hỏi ngân sách sau đó
    assert state.stage == "collecting" and state.asked[-1] == "ngân sách"


def test_greeting_first_message_lists_categories():
    llm = FakeLLM()
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "xin chào")
    assert res.stage == "collecting"
    assert "tủ lạnh" in res.reply.lower()
    assert llm.calls == []  # meta không gọi LLM


def test_capability_question_answered_without_llm():
    llm = FakeLLM()
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "bạn tư vấn được những gì?")
    assert "tủ lạnh" in res.reply.lower() and "màn hình" in res.reply.lower()
    assert llm.calls == []


def test_thanks_after_recommendation_keeps_state():
    orch = Orchestrator(store(), FakeLLM())
    state = ChatState(profile=NeedProfile(category="tu_lanh"), asked=[], stage="recommended",
                      last_top_price=12_000_000)
    state, res = orch.handle_turn(state, "ok cảm ơn em nhé")
    assert res.stage == "recommended"
    assert "cảm ơn" in res.reply.lower()
    assert state.last_top_price == 12_000_000
