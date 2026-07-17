from app.advice.provenance import build_fact_card, format_vnd, facts_for_llm
from app.schemas import Product, SourcedValue, ScoredProduct, NeedProfile


def mk():
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code="DK1", sku="DK1",
                brand="Daikin", display_name="Tủ lạnh Daikin Inverter 313",
                price=SourcedValue.of(12_400_000, "catalog", detail="giá khuyến mãi"),
                original_price=SourcedValue.of(12_900_000, "catalog"),
                sale_price=SourcedValue.of(12_400_000, "catalog"),
                specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất", unit="kWh/năm")},
                spec_doc="", promo_text="Miễn phí lắp đặt", raw={})
    return ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 1.0}, matched=["tiết kiệm điện"])


def test_format_vnd():
    assert format_vnd(14990000) == "14.990.000đ"


def test_fact_card_has_sourced_lines_and_missing():
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    card = build_fact_card(mk(), prof)
    labels = [l.label for l in card.lines]
    assert "Giá" in labels
    assert any(l.source == "catalog" for l in card.lines)
    assert any(l.source == "thông số nhà sản xuất" for l in card.lines)
    # dữ liệu không có phải được liệt kê thẳng
    assert "tồn kho" in card.missing
    assert "đánh giá người dùng (review)" in card.missing


def test_facts_for_llm_only_contains_sourced_values():
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    facts = facts_for_llm([build_fact_card(mk(), prof)])
    assert "12.400.000đ" in facts
    assert "300" in facts
    assert "tồn kho" in facts.lower()   # nêu rõ phần chưa có dữ liệu


def test_matched_but_unavailable_spec_disclosed_in_missing():
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code="X", sku="X",
                brand="Daikin", display_name="Tủ lạnh Daikin",
                price=SourcedValue.of(12_000_000, "catalog"),
                original_price=SourcedValue.of(12_000_000, "catalog"),
                sale_price=SourcedValue.missing(),
                specs={"Điện năng tiêu thụ": SourcedValue.missing()},  # relevant to "tiết kiệm điện" but absent
                spec_doc="", promo_text=None, raw={})
    sp = ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 0.0}, matched=["tiết kiệm điện"])
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    card = build_fact_card(sp, prof)
    assert "Điện năng tiêu thụ" in card.missing


def test_whole_float_spec_rendered_without_decimal():
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code="X", sku="X",
                brand="LG", display_name="Tủ lạnh LG",
                price=SourcedValue.of(12_000_000, "catalog"),
                original_price=SourcedValue.of(12_000_000, "catalog"), sale_price=SourcedValue.missing(),
                specs={"Điện năng tiêu thụ": SourcedValue.of(436.0, "thông số nhà sản xuất", unit="kWh/năm")},
                spec_doc="", promo_text=None, raw={})
    sp = ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 1.0}, matched=["tiết kiệm điện"])
    card = build_fact_card(sp, NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"]))
    vals = [l.value for l in card.lines]
    assert "436 kWh/năm" in vals and "436.0 kWh/năm" not in vals
