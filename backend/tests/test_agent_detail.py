from app.llm.client import FakeLLM
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)


def _rows():
    return [{"model_code": "A", "brand": "Toshiba", "price_clean": 12_000_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "300 lít"}'},
            {"model_code": "B", "brand": "LG", "price_clean": 11_000_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "250 lít"}'}]


def test_is_detail_question():
    assert is_detail_question("máy này bảo hành thế nào") is True
    assert is_detail_question("mua tủ lạnh 20 triệu") is False


def test_resolve_by_position():
    assert resolve_product_row("cho em xem kỹ máy 2", _rows())["brand"] == "LG"


def test_resolve_by_brand():
    assert resolve_product_row("con toshiba dung tích bao nhiêu", _rows())["brand"] == "Toshiba"


def test_resolve_by_superlative_price():
    assert resolve_product_row("cái rẻ nhất có tốt không", _rows())["brand"] == "LG"


def test_answer_grounded_passthrough():
    llm = FakeLLM(text_responses=["Dạ máy Toshiba dung tích 300 lít ạ."])
    msg, card = answer_detail(_rows()[0], "dung tích bao nhiêu", llm)
    assert "300" in msg
    assert card.title.startswith("Thông tin chi tiết")


def test_answer_fail_closed_on_hallucination():
    # LLM bịa số 999 không có trong fact-sheet -> phải bị thay bằng safe summary.
    llm = FakeLLM(text_responses=["Máy này chỉ 999 lít và giá 5000000đ."])
    msg, card = answer_detail(_rows()[0], "thông số", llm)
    assert "999" not in msg


def test_wants_product_list():
    assert wants_product_list("cho xem máy khác đi") is True
    assert wants_product_list("máy này bao nhiêu tiền") is False
