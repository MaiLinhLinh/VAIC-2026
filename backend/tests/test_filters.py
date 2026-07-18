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


def test_numeric_range_constraint_enforces_minimum_bound():
    ps = [
        Product(category="Màn hình máy tính", category_code="man_hinh", model_code="a", sku="a",
                brand="A", display_name="Màn hình A",
                price=SourcedValue.of(4_000_000, "catalog"),
                original_price=SourcedValue.of(4_000_000, "catalog"), sale_price=SourcedValue.missing(),
                specs={"Kích thước màn hình": SourcedValue.of(14, "thông số nhà sản xuất")},
                spec_doc="", promo_text=None, raw={}),
        Product(category="Màn hình máy tính", category_code="man_hinh", model_code="b", sku="b",
                brand="B", display_name="Màn hình B",
                price=SourcedValue.of(5_000_000, "catalog"),
                original_price=SourcedValue.of(5_000_000, "catalog"), sale_price=SourcedValue.missing(),
                specs={"Kích thước màn hình": SourcedValue.of(15.6, "thông số nhà sản xuất")},
                spec_doc="", promo_text=None, raw={}),
    ]
    prof = NeedProfile(category="man_hinh", constraints={"kích thước": [15, None]})
    out = apply_hard_filters(ps, prof)
    assert len(out) == 1 and out[0].display_name == "Màn hình B"
