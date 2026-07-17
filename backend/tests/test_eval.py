from app.eval_utils import evaluate  # sẽ tạo ở Step 3 (module dùng chung)
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue


def _store():
    def mk(brand, price):
        return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                       brand=brand, display_name=f"Tủ lạnh {brand}",
                       price=SourcedValue.of(price, "catalog"),
                       original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                       specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất"),
                              "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất")},
                       spec_doc="", promo_text=None, raw={})
    return ProductStore([mk("A", 12_000_000), mk("B", 11_000_000)])


def test_evaluate_reports_category_accuracy():
    scenarios = [{"message": "tu lanh 20tr tiet kiem dien", "expect_category": "tu_lanh",
                  "expect_budget_max": 20000000, "expect_prefs": ["tiết kiệm điện"]}]
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "prefs"]}])
    m = evaluate(scenarios, llm, _store())
    assert m["category_acc"] == 1.0
    assert m["budget_acc"] == 1.0
    assert m["pref_recall"] == 1.0
