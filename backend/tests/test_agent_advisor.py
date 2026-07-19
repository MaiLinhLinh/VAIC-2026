from app.llm.client import FakeLLM
from app.agent_core.advisor import build_cards, generate_advisor


def _rows():
    return [{"model_code": "A", "brand": "Toshiba", "price_clean": 12_400_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "300 lít"}'},
            {"model_code": "B", "brand": "LG", "price_clean": 11_000_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "250 lít"}'}]


def test_build_cards_titles():
    cards = build_cards(_rows(), ["tiết kiệm điện"])
    assert cards[0].title.startswith("Vì sao em đề xuất")
    assert len(cards) == 2


def test_generate_blocking_grounded():
    llm = FakeLLM(text_responses=["Máy Toshiba giá 12.400.000đ, dung tích 300 lít, rất phù hợp."])
    cards = build_cards(_rows(), [])
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"priority_features": []},
                                               _rows(), "exact_match", llm, cards)
    assert "12.400.000" in msg
    assert streamed is False
    assert warnings == []


def test_generate_fail_closed_when_ungrounded():
    llm = FakeLLM(text_responses=["Giá chỉ 5.555.555đ thôi ạ."])   # số không có trong cards
    cards = build_cards(_rows(), [])
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"priority_features": []},
                                               _rows(), "exact_match", llm, cards)
    assert "5.555.555" not in msg    # đã fail-closed thay bằng safe summary
    assert warnings and warnings[0].startswith("Số chưa truy được nguồn")


def test_streaming_emits_verified_lines():
    llm = FakeLLM(text_responses=["Máy Toshiba giá 12.400.000đ.\nRất bền và đẹp.\n"])
    cards = build_cards(_rows(), [])
    got = []
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"priority_features": []},
                                               _rows(), "exact_match", llm, cards, on_delta=got.append)
    assert streamed is True
    assert "".join(got).strip().startswith("Máy Toshiba")


def test_no_products_deterministic():
    llm = FakeLLM(text_responses=["should not be used"])
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"category": "Tủ Lạnh"},
                                               [], "no_products_found", llm, [])
    assert "không tìm thấy sản phẩm" in msg.lower()
    assert streamed is False


def test_relaxed_preferences_discloses_unverified_features_without_llm():
    cards = build_cards(_rows(), ["gọi điện"])
    llm = FakeLLM(text_responses=["Không được sử dụng"])
    msg, streamed, warnings = generate_advisor(
        "top sản phẩm", {"priority_features": ["gọi điện"],
                         "relaxed_features": ["gọi điện", "SOS"]},
        _rows(), "relaxed_preferences", llm, cards,
    )

    assert "chưa xác nhận đầy đủ" in msg
    assert "không khẳng định" in msg
    assert "gọi điện, SOS" in msg
    assert llm.calls == []
    assert streamed is False
    assert warnings == []
