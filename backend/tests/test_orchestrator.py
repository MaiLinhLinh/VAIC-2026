from app.orchestrator import Orchestrator, ChatState
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue, NeedProfile


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


def test_asks_when_missing_critical_slot():
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "prefs"]}])
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "mua tu lanh duoi 20tr tiet kiem dien")
    assert res.question is not None and res.stage == "collecting"
    assert "người" in res.question.lower()


def test_recommends_when_enough_info_and_grounded():
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000, "constraints": {"số người": [3, 4]},
                         "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "constraints", "prefs"]}],
        text_responses=["Với gia đình 3-4 người ưu tiên tiết kiệm điện, em gợi ý các máy có giá 12.000.000đ và 11.000.000đ."])
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "nha 4 nguoi, tu lanh duoi 20tr, tiet kiem dien")
    assert res.stage == "recommended" and res.advice is not None
    assert res.advice.cards


def test_fail_closed_when_llm_hallucinates_number():
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000, "constraints": {"số người": [3, 4]},
                         "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "constraints", "prefs"]}],
        text_responses=["Máy này chỉ 999.999đ, quá rẻ!"])
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "nha 4 nguoi, tu lanh duoi 20tr, tiet kiem dien")
    assert "999.999" not in res.reply
    assert res.advice is not None


def test_budget_down_intent_returns_cheaper_alternatives():
    st = ProductStore([mk("A", 12_000_000, 300), mk("B", 11_000_000, 400), mk("C", 7_000_000, 380)])
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000, "constraints": {"số người": [3, 4]},
                         "prefs": [], "known": ["category", "budget_max", "constraints"]}],
        text_responses=["Em gợi ý các máy phù hợp."])
    orch = Orchestrator(st, llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "nha 4 nguoi tu lanh duoi 20tr")
    assert res.stage == "recommended" and state.last_top_price == 12_000_000
    state, res2 = orch.handle_turn(state, "co cach nao re hon khong em")
    assert res2.stage == "recommended" and res2.advice is not None
    assert any("7.000.000" in l.value for c in res2.advice.cards for l in c.lines)
