from app.retrieval.engine import RetrievalEngine, query_from_profile
from app.catalog.loader import ProductStore
from app.schemas import Product, SourcedValue, NeedProfile


def mk(code, brand, price, dien):
    return Product(category="Tủ lạnh", category_code=code, model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs={"Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất"),
                          "Công nghệ tiết kiệm điện": SourcedValue.of("Inverter", "thông số nhà sản xuất")},
                   spec_doc=f"{brand} inverter", promo_text=None, raw={})


def test_engine_end_to_end_ranks_and_filters():
    store = ProductStore([
        mk("tu_lanh", "A", 12_000_000, 300),
        mk("tu_lanh", "B", 11_000_000, 400),
        mk("tu_lanh", "C", 25_000_000, 250),   # ngoài ngân sách
    ])
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, prefs=["tiết kiệm điện"])
    reco = RetrievalEngine(store).recommend(prof)
    brands = [s.product.brand for s in reco.top3]
    assert "C" not in brands                 # bị loại vì ngân sách
    assert reco.top3[0].product.brand == "A" # tiết kiệm điện nhất trong tầm giá


def test_query_from_profile():
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện", "ít ồn"], demographics={"đối tượng": "gia đình"})
    q = query_from_profile(prof)
    assert "tiết kiệm điện" in q and "gia đình" in q
