from app.advice.detail import (
    resolve_product, build_full_fact_card, answer_about_product, is_detail_question,
)
from app.orchestrator import Orchestrator, ChatState
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue


def mk(brand, price, dien, dungtich):
    return Product(
        category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku="SKU-" + brand,
        brand=brand, display_name=f"Tủ lạnh {brand} {dungtich} lít",
        price=SourcedValue.of(price, "catalog"),
        original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
        specs={"Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất", unit="kWh/năm"),
               "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất")},
        spec_doc="", promo_text="Miễn phí lắp đặt",
        raw={"brand": brand, "Dung tích tổng": f"{dungtich} lít", "Điện năng tiêu thụ": str(dien),
             "Kiểu dáng": "Ngăn đá dưới", "giá gốc": price})


def test_resolve_by_position():
    prods = [mk("Aqua", 1, 1, 1), mk("Sam", 1, 1, 1), mk("Panas", 1, 1, 1)]
    assert resolve_product("cho em hoi ky ve may 2", prods).brand == "Sam"
    assert resolve_product("cai dau tien the nao", prods).brand == "Aqua"
    assert resolve_product("may cuoi cung ra sao", prods).brand == "Panas"


def test_resolve_by_brand():
    prods = [mk("Samsung", 1, 1, 1), mk("Casper", 1, 1, 1)]
    assert resolve_product("may Casper bao hanh sao", prods).brand == "Casper"


def test_resolve_by_superlative_price():
    prods = [mk("Sam", 12_000_000, 1, 1), mk("Casper", 9_000_000, 1, 1)]
    assert resolve_product("cai re nhat co tot khong", prods).brand == "Casper"
    assert resolve_product("cai dat nhat the nao", prods).brand == "Sam"


def test_resolve_by_explicit_mentioned_price():
    prods = [mk("Samsung", 150_000, 1, 1), mk("Zwatch", 490_000, 1, 1)]
    assert resolve_product("cái 150k có nghe gọi được không", prods).brand == "Samsung"


def test_call_detail_answer_is_deterministic_and_fails_closed():
    product = mk("Samsung", 150_000, 1, 1)
    product.specs["Thực hiện cuộc gọi"] = SourcedValue.of("Không", "thông số nhà sản xuất")
    product.raw["Thực hiện cuộc gọi"] = "Không"
    fake = FakeLLM(text_responses=["Bịa là máy này nghe gọi được."])

    result = answer_about_product(product, "150k mà nghe gọi được à?", fake)

    assert "Dạ không" in result.message
    assert "catalog ghi" in result.message.lower()
    assert fake.calls == []


def test_resolve_none_when_no_reference():
    prods = [mk("Sam", 1, 1, 1), mk("Casper", 1, 1, 1)]
    assert resolve_product("con nhu the nao nhi", prods) is None


def test_build_full_fact_card_grounded_and_missing():
    card = build_full_fact_card(mk("Samsung", 14_990_000, 381, 313))
    labels = [l.label for l in card.lines]
    assert "Giá" in labels and "Thương hiệu" in labels
    assert "Dung tích tổng" in labels and "Kiểu dáng" in labels     # thông số thật từ raw
    assert "giá gốc" not in labels and "brand" not in labels        # cột id/giá không lặp
    assert any(l.value == "14.990.000đ" for l in card.lines)
    assert "tồn kho" in card.missing and "trả góp" in card.missing


def test_answer_about_product_grounded():
    fake = FakeLLM(text_responses=["Dạ Tủ lạnh Samsung 313 lít có dung tích 313 lít, giá 14.990.000đ ạ."])
    res = answer_about_product(mk("Samsung", 14_990_000, 381, 313), "dung tich bao nhieu", fake)
    assert "313" in res.message                       # grounded -> giữ nguyên câu LLM
    assert res.cards[0].title.startswith("Thông tin chi tiết")


def test_answer_about_product_fail_closed_on_invented_number():
    fake = FakeLLM(text_responses=["Máy này chỉ 999.999đ, siêu rẻ!"])   # bịa số
    res = answer_about_product(mk("Samsung", 14_990_000, 381, 313), "gia bao nhieu", fake)
    assert "999.999" not in res.message               # fail-closed
    assert res.cards                                  # vẫn kèm fact-sheet


def test_orchestrator_deepdive_answers_specific_product_not_rerecommend():
    store = ProductStore([mk("Aqua", 12_000_000, 300, 313),
                          mk("Sam", 11_000_000, 400, 320),
                          mk("Panas", 9_000_000, 380, 300)])
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                         "constraints": {"số người": [3, 4]}, "prefs": [],
                         "known": ["category", "budget_max", "constraints"]}],
        text_responses=["Em gợi ý 3 máy.",                          # lượt đề xuất
                        "Dạ Tủ lạnh Sam 320 lít, dung tích 320 lít ạ."])  # lượt hỏi chi tiết
    orch = Orchestrator(store, llm)
    state = ChatState()
    state, r1 = orch.handle_turn(state, "nha 4 nguoi tu lanh 20tr")
    assert r1.stage == "recommended" and len(state.last_products) == 3

    state, r2 = orch.handle_turn(state, "cho em hoi ky ve may 2")
    assert r2.stage == "recommended"
    assert state.focused_sku == state.last_products[1].sku            # đúng máy thứ 2 (Sam)
    assert r2.advice.cards[0].title.startswith("Thông tin chi tiết")
    assert "320" in r2.reply                                          # trả lời VỀ máy 2, không đề xuất lại
    assert r2.advice.comparison is None                              # 1 máy -> không có bảng so sánh


def test_orchestrator_deepdive_multiturn_uses_focused_product():
    store = ProductStore([mk("Aqua", 12_000_000, 300, 313), mk("Sam", 11_000_000, 400, 320)])
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                         "constraints": {"số người": [3, 4]}, "prefs": [],
                         "known": ["category", "budget_max", "constraints"]}],
        text_responses=["Em gợi ý 2 máy.",
                        "Dạ máy Aqua giá 12.000.000đ ạ.",
                        "Dạ máy Aqua có kiểu dáng Ngăn đá dưới ạ."])
    orch = Orchestrator(store, llm)
    state = ChatState()
    state, _ = orch.handle_turn(state, "nha 4 nguoi tu lanh 20tr")
    state, r2 = orch.handle_turn(state, "cai dau tien gia bao nhieu")   # focus Aqua
    assert state.focused_sku == store.by_category("tu_lanh")[0].sku
    state, r3 = orch.handle_turn(state, "the con kieu dang thi sao")    # follow-up, không nêu tên máy
    assert r3.stage == "recommended"
    assert r3.advice.cards[0].title.endswith(store.by_category("tu_lanh")[0].display_name)
