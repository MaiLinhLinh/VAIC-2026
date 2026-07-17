from app.retrieval.scoring import score_products, select_top3, why_not_group
from app.schemas import Product, SourcedValue, NeedProfile


def mk(brand, price, dien, inverter="Digital Inverter"):
    specs = {
        "Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất") if dien is not None else SourcedValue.missing(),
        "Công nghệ tiết kiệm điện": SourcedValue.of(inverter, "thông số nhà sản xuất") if inverter else SourcedValue.missing(),
        "Dung tích tổng": SourcedValue.of(300, "thông số nhà sản xuất"),
    }
    return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs=specs, spec_doc="", promo_text=None, raw={})


def test_energy_saving_pref_scores_lower_consumption_higher():
    cands = [mk("A", 12_000_000, 300), mk("B", 11_000_000, 400)]
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    scored = score_products(cands, prof)
    top = sorted(scored, key=lambda s: s.score, reverse=True)[0]
    assert top.product.brand == "A"                 # 300 kWh < 400 kWh -> điểm cao hơn
    assert "tiết kiệm điện" in top.matched


def test_select_top3_prefers_brand_diversity():
    cands = [mk("A", 12_000_000, 300), mk("A", 12_500_000, 310), mk("B", 11_000_000, 320), mk("C", 9_000_000, 330)]
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    top3 = select_top3(score_products(cands, prof))
    brands = [s.product.brand for s in top3]
    assert len(top3) == 3 and len(set(brands)) >= 2


def test_why_not_group_for_energy_pref():
    cands = [mk("A", 12_000_000, 300, inverter="Digital Inverter"),
             mk("D", 7_000_000, 500, inverter=None)]     # không inverter
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    grp = why_not_group(cands, prof)
    assert grp is not None and "không inverter" in grp.label
    assert "tiết kiệm điện" in grp.reason
