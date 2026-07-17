from app.schemas import SourcedValue, NeedProfile, Product


def test_sourcedvalue_of_and_missing():
    sv = SourcedValue.of(313, "thông số nhà sản xuất", unit="lít")
    assert sv.available and sv.value == 313 and sv.unit == "lít"
    assert sv.provenance.source == "thông số nhà sản xuất"
    m = SourcedValue.missing()
    assert m.available is False and m.value is None and m.note == "chưa có dữ liệu"


def test_needprofile_merge_keeps_known():
    a = NeedProfile(category="tu_lanh", budget_max=20_000_000, known=["category", "budget_max"])
    b = NeedProfile(category=None, constraints={"số người": [3, 4]}, known=["constraints"])
    merged = a.merge(b)
    assert merged.category == "tu_lanh"
    assert merged.budget_max == 20_000_000
    assert merged.constraints == {"số người": [3, 4]}
    assert set(merged.known) == {"category", "budget_max", "constraints"}


def test_product_number_helper():
    p = Product(
        category="Tủ Lạnh", category_code="tu_lanh", model_code="1", sku="1",
        brand="Samsung", display_name="Tủ lạnh Samsung",
        price=SourcedValue.of(14990000, "catalog"),
        original_price=SourcedValue.of(14990000, "catalog"),
        sale_price=SourcedValue.missing(),
        specs={"Dung tích tổng": SourcedValue.of(313, "thông số nhà sản xuất", unit="lít"),
               "Điện năng tiêu thụ": SourcedValue.missing()},
        spec_doc="", promo_text=None, raw={},
    )
    assert p.number("Dung tích tổng") == 313
    assert p.number("Điện năng tiêu thụ") is None
    assert p.number("không tồn tại") is None
