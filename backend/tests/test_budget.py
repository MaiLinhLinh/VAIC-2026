from app.advice.budget import budget_alternatives, describe_tradeoff
from app.catalog.loader import ProductStore
from app.schemas import Product, SourcedValue, NeedProfile, ScoredProduct


def mk(brand, price):
    return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs={"Điện năng tiêu thụ": SourcedValue.of(350, "thông số nhà sản xuất")},
                   spec_doc="", promo_text=None, raw={})


def test_budget_down_returns_cheaper():
    store = ProductStore([mk("A", 12_000_000), mk("B", 8_900_000), mk("C", 7_500_000)])
    prof = NeedProfile(category="tu_lanh", budget_max=15_000_000, prefs=[])
    alts = budget_alternatives(prof, store, direction="down")
    assert alts and all(a.product.price.value <= 12_000_000 for a in alts)
    assert any(a.product.price.value <= 8_900_000 for a in alts)


def test_describe_tradeoff_price_delta():
    cheaper = ScoredProduct(product=mk("B", 8_900_000), score=0.0)
    txt = describe_tradeoff(cheaper, current_price=12_400_000)
    assert "3.500.000đ" in txt   # 12.4tr - 8.9tr
