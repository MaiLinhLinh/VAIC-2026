from app.advice.compare import build_comparison
from app.advice.generate import generate_advice
from app.schemas import Product, SourcedValue, ScoredProduct, NeedProfile, Recommendation
from app.llm.client import FakeLLM


def mk(brand, price, dien):
    return Product(
        category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
        brand=brand, display_name=f"Tủ lạnh {brand}",
        price=SourcedValue.of(price, "catalog") if price is not None else SourcedValue.missing(),
        original_price=SourcedValue.missing(), sale_price=SourcedValue.missing(),
        specs={"Điện năng tiêu thụ": (SourcedValue.of(dien, "thông số nhà sản xuất", unit="kWh/năm")
                                      if dien is not None else SourcedValue.missing())},
        spec_doc="", promo_text=None, raw={})


def sp(brand, price, dien):
    return ScoredProduct(product=mk(brand, price, dien), score=1.0, breakdown={}, matched=["tiết kiệm điện"])


def test_comparison_has_price_pref_and_brand_rows_with_best_marked():
    scored = [sp("A", 12_000_000, 300), sp("B", 11_000_000, 400)]
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    table = build_comparison(scored, prof)
    assert table is not None
    assert table.products == ["Tủ lạnh A", "Tủ lạnh B"]
    labels = [r.label for r in table.rows]
    assert "Giá" in labels and "Điện năng tiêu thụ" in labels and "Thương hiệu" in labels

    # B rẻ hơn (11tr < 12tr) -> best ở cột B (index 1)
    price_row = next(r for r in table.rows if r.label == "Giá")
    assert price_row.cells[1].is_best and not price_row.cells[0].is_best
    assert price_row.cells[1].value == "11.000.000đ"

    # A tiết kiệm điện hơn (300 < 400, hướng min) -> best ở cột A (index 0)
    energy_row = next(r for r in table.rows if r.label == "Điện năng tiêu thụ")
    assert energy_row.cells[0].is_best and not energy_row.cells[1].is_best
    assert energy_row.cells[0].value == "300 kWh/năm"


def test_comparison_marks_missing_data_and_best_only_over_available():
    scored = [sp("A", None, 300), sp("B", 11_000_000, None)]  # A thiếu giá; B thiếu điện năng
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    table = build_comparison(scored, prof)

    price_row = next(r for r in table.rows if r.label == "Giá")
    assert price_row.cells[0].value == "chưa có dữ liệu" and price_row.cells[0].available is False
    assert price_row.cells[1].is_best  # chỉ B có giá -> B tốt nhất

    energy_row = next(r for r in table.rows if r.label == "Điện năng tiêu thụ")
    assert energy_row.cells[1].value == "chưa có dữ liệu"
    assert energy_row.cells[0].is_best  # chỉ A có điện năng -> A tốt nhất


def test_comparison_needs_at_least_two_candidates():
    scored = [sp("A", 12_000_000, 300)]
    assert build_comparison(scored, NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])) is None


def test_comparison_marks_all_tied_best():
    # A và B cùng 300 kWh -> cả hai đều là "tốt nhất" về tiết kiệm điện
    scored = [sp("A", 12_000_000, 300), sp("B", 11_000_000, 300)]
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    table = build_comparison(scored, prof)
    energy_row = next(r for r in table.rows if r.label == "Điện năng tiêu thụ")
    assert energy_row.cells[0].is_best and energy_row.cells[1].is_best


def test_generate_advice_attaches_comparison():
    reco = Recommendation(top3=[sp("A", 12_000_000, 300), sp("B", 11_000_000, 400)],
                          excluded=None, assumptions=[])
    fake = FakeLLM(text_responses=["So sánh nhanh 2 máy..."])
    res = generate_advice(reco, NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"]), fake)
    assert res.comparison is not None
    assert res.comparison.products == ["Tủ lạnh A", "Tủ lạnh B"]
    assert any(r.label == "Điện năng tiêu thụ" for r in res.comparison.rows)
