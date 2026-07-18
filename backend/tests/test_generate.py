from app.llm.client import FakeLLM
from app.advice.generate import generate_advice
from app.schemas import Product, SourcedValue, ScoredProduct, Recommendation, NeedProfile, ExcludedGroup


def sp(brand, price):
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                brand=brand, display_name=f"Tủ lạnh {brand}",
                price=SourcedValue.of(price, "catalog"),
                original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất", unit="kWh/năm")},
                spec_doc="", promo_text=None, raw={})
    return ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 1.0}, matched=["tiết kiệm điện"])


def test_generate_advice_builds_cards_and_message():
    reco = Recommendation(top3=[sp("Daikin", 12_400_000), sp("Panasonic", 11_500_000)],
                          excluded=ExcludedGroup(label="máy non-inverter", reason="vì ưu tiên tiết kiệm điện"),
                          assumptions=["Em tạm tính phòng không nắng."])
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    fake = FakeLLM(text_responses=["Với nhu cầu tiết kiệm điện, em đề xuất 2 máy..."])
    result = generate_advice(reco, prof, fake)
    assert "đề xuất" in result.message
    assert len(result.cards) == 2
    assert result.assumptions == ["Em tạm tính phòng không nắng."]
    # facts phải được đưa vào prompt gửi LLM
    sys, user = fake.calls[0]
    assert "12.400.000đ" in user and "tồn kho" in user.lower()


def test_generate_advice_empty_top3_no_llm_call():
    reco = Recommendation(top3=[], excluded=None, assumptions=[])
    prof = NeedProfile(category="man_hinh", budget_max=15_000_000, constraints={"kích thước": [15, None]})
    fake = FakeLLM(text_responses=["should not be used"])
    result = generate_advice(reco, prof, fake)
    assert "chưa tìm được" in result.message.lower()
    assert "15 inch" in result.message
    assert fake.calls == []          # không gọi LLM khi rỗng
