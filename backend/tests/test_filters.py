from app.retrieval.filters import apply_hard_filters
from app.schemas import Product, SourcedValue, NeedProfile


def mk(code, price, people=None):
    specs = {}
    if people is not None:
        specs["Số người sử dụng"] = SourcedValue.of(list(people), "thông số nhà sản xuất")
    return Product(category="Tủ lạnh", category_code=code, model_code="m", sku="s",
                   brand="B", display_name="x",
                   price=SourcedValue.of(price, "catalog") if price else SourcedValue.missing(),
                   original_price=SourcedValue.missing(), sale_price=SourcedValue.missing(),
                   specs=specs, spec_doc="", promo_text=None, raw={})


def test_budget_filter():
    ps = [mk("tu_lanh", 12_000_000), mk("tu_lanh", 25_000_000), mk("tu_lanh", None)]
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000)
    out = apply_hard_filters(ps, prof)
    assert len(out) == 1 and out[0].price.value == 12_000_000


def test_people_constraint_overlap():
    ps = [mk("tu_lanh", 10_000_000, people=(3, 4)), mk("tu_lanh", 10_000_000, people=(1, 2))]
    prof = NeedProfile(category="tu_lanh", constraints={"số người": [4, 5]})
    out = apply_hard_filters(ps, prof)
    assert len(out) == 1 and out[0].specs["Số người sử dụng"].value == [3, 4]
