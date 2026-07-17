from app.advice.verify import extract_numbers, allowed_numbers, verify_advice, is_grounded
from app.schemas import AdviceResult, FactCard, FactLine


def cards():
    return [FactCard(title="t", lines=[
        FactLine(label="Giá", value="12.400.000đ", source="catalog"),
        FactLine(label="Điện năng tiêu thụ", value="300 kWh/năm", source="thông số nhà sản xuất"),
    ], missing=["tồn kho"])]


def test_extract_and_allowed():
    nums = allowed_numbers(cards())
    assert "12400000" in nums and "300" in nums


def test_verify_flags_ungrounded_number():
    res = AdviceResult(message="Máy này chỉ 9.990.000đ, tiết kiệm 300 kWh.", cards=cards())
    out = verify_advice(res)
    assert not is_grounded(out)
    assert any("9990000" in w for w in out.warnings)   # 9.990.000 không có nguồn


def test_verify_passes_when_all_grounded():
    res = AdviceResult(message="Máy này giá 12.400.000đ, điện 300 kWh/năm.", cards=cards())
    out = verify_advice(res)
    assert is_grounded(out) and out.warnings == []
