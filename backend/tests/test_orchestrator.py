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


def mk_dishwasher(brand, price, water):
    return Product(
        category="Máy rửa chén",
        category_code="may_rua_chen",
        model_code=brand,
        sku=brand,
        brand=brand,
        display_name=f"Máy rửa chén {brand}",
        price=SourcedValue.of(price, "catalog"),
        original_price=SourcedValue.of(price, "catalog"),
        sale_price=SourcedValue.missing(),
        specs={
            "Tiêu thụ nước": SourcedValue.of(
                water, "thông số nhà sản xuất", unit="lít/lần"
            )
        },
        spec_doc="",
        promo_text=None,
        raw={},
    )


def mk_monitor(brand, price, size, response_time):
    return Product(
        category="Màn hình máy tính",
        category_code="man_hinh",
        model_code=brand,
        sku=brand,
        brand=brand,
        display_name=f"Màn hình {brand} {size} inch",
        price=SourcedValue.of(price, "catalog"),
        original_price=SourcedValue.of(price, "catalog"),
        sale_price=SourcedValue.missing(),
        specs={
            "Kích thước màn hình": SourcedValue.of(
                size, "thông số nhà sản xuất", unit="inch"
            ),
            "Thời gian đáp ứng": SourcedValue.of(
                response_time, "thông số nhà sản xuất", unit="ms"
            ),
        },
        spec_doc="",
        promo_text=None,
        raw={},
    )


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


def test_budget_up_intent_returns_pricier_alternatives():
    st = ProductStore([mk("A", 12_000_000, 300), mk("B", 15_000_000, 250), mk("C", 18_000_000, 240)])
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 13000000, "constraints": {"số người": [3, 4]},
                         "prefs": [], "known": ["category", "budget_max", "constraints"]}],
        text_responses=["Em gợi ý máy phù hợp."])
    orch = Orchestrator(st, llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "tu lanh cho nha 4 nguoi tam 13 trieu")
    assert res.stage == "recommended" and state.last_top_price == 12_000_000
    state, res2 = orch.handle_turn(state, "co loai nao cao cap hon khong em")
    assert res2.stage == "recommended" and res2.advice is not None
    assert any("15.000.000" in l.value for c in res2.advice.cards for l in c.lines)


def test_empty_result_follow_up_returns_minimum_budget_and_named_product():
    st = ProductStore([mk("A", 12_000_000, 300), mk("B", 11_000_000, 400)])
    llm = FakeLLM(
        json_responses=[{
            "category": "tu_lanh", "budget_max": 5_000_000,
            "constraints": {"số người": [3, 4]},
            "prefs": ["tiết kiệm điện"],
            "known": ["category", "budget_max", "constraints", "prefs"],
        }],
    )
    orch = Orchestrator(st, llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")

    state, first = orch.handle_turn(state, "tủ lạnh dưới 5tr cho nhà 4 người, tiết kiệm điện")
    assert first.advice is not None and not first.advice.cards
    assert "chưa tìm được" in first.reply
    assert first.advice.warnings == []

    state, follow_up = orch.handle_turn(
        state, "vậy ngân sách tối thiểu cho option trên là bao nhiêu với máy nào"
    )

    assert "11.000.000đ" in follow_up.reply
    assert "Tủ lạnh B" in follow_up.reply
    assert follow_up.advice is not None and follow_up.advice.cards


def test_30m_quay_dau_dishwasher_flow_survives_wrong_llm_budget_direction():
    st = ProductStore([
        mk_dishwasher("Comfee", 5_990_000, 8.0),
        mk_dishwasher("Bosch", 16_590_000, 9.0),
    ])
    llm = FakeLLM(
        json_responses=[
            {
                "category": None,
                "budget_min": 30_000_000,
                "budget_max": None,
                "constraints": {},
                "prefs": [],
                "known": ["budget_min"],
            },
            {
                "category": None,
                "budget_min": None,
                "budget_max": None,
                "constraints": {},
                "prefs": ["tiết kiệm nước"],
                "known": ["prefs"],
            },
        ],
        text_responses=["Dạ em đã tìm được các máy phù hợp với ưu tiên tiết kiệm nước."],
    )
    orch = Orchestrator(st, llm)
    state = ChatState(
        profile=NeedProfile(category="may_rua_chen", constraints={"số người": [4, 4]}),
        asked=["số bữa"],
        stage="collecting",
    )

    state, budget_turn = orch.handle_turn(state, "khoảng 30tr quay đầu")
    assert state.profile.budget_min is None
    assert state.profile.budget_max == 30_000_000
    assert budget_turn.question is not None and "ưu tiên" in budget_turn.question.lower()

    state, preference_turn = orch.handle_turn(state, "tiết kiệm nước")
    assert preference_turn.advice is not None
    assert preference_turn.advice.cards


def test_large_screen_answer_completes_clarification_instead_of_repeating_question():
    llm = FakeLLM(
        json_responses=[
            {
                "category": "man_hinh",
                "budget_max": None,
                "constraints": {},
                "prefs": [],
                "known": ["category"],
            },
            {
                "category": None,
                "budget_max": None,
                "constraints": {},
                "prefs": [],
                "known": [],
            },
            {
                "category": None,
                "budget_min": 15_000_000,
                "budget_max": 15_000_000,
                "constraints": {},
                "prefs": [],
                "known": ["budget_min", "budget_max"],
            },
            {
                "category": None,
                "budget_max": None,
                "constraints": {},
                "prefs": [],
                "known": [],
            },
        ],
        text_responses=["Dạ em đã tìm được các màn hình phù hợp với nhu cầu của anh/chị."],
    )
    orch = Orchestrator(ProductStore([
        mk_monitor("A", 8_000_000, 27, 1),
        mk_monitor("B", 12_000_000, 32, 1),
        mk_monitor("C", 14_000_000, 34, 0.5),
    ]), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")

    state, res1 = orch.handle_turn(state, "mua màn hình")
    assert res1.question is not None and "làm gì" in res1.question.lower()

    state, res2 = orch.handle_turn(state, "game")
    assert res2.question is not None and "ngân sách" in res2.question.lower()
    assert state.profile.prefs == ["chơi game"]

    state, res3 = orch.handle_turn(state, "15 củ")
    assert res3.question is not None and "inch" in res3.question.lower()
    assert state.profile.budget_min is None
    assert state.profile.budget_max == 15_000_000

    state, res4 = orch.handle_turn(state, "càng to càng tốt")
    assert res4.stage == "recommended"
    assert res4.question is None
    assert res4.advice is not None
    assert len(res4.advice.cards) == 3
    assert res4.advice.warnings == []
    assert state.profile.prefs == ["chơi game", "màn hình lớn"]
