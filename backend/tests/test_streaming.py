from app.llm.client import FakeLLM
from app.advice.streaming import stream_advice
from app.advice.verify import is_grounded
from app.schemas import Product, SourcedValue, ScoredProduct, Recommendation, NeedProfile


def sp(brand, price):
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                brand=brand, display_name=f"Tủ lạnh {brand}",
                price=SourcedValue.of(price, "catalog"),
                original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất", unit="kWh/năm")},
                spec_doc="", promo_text=None, raw={})
    return ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 1.0},
                         matched=["tiết kiệm điện"])


def _reco():
    return Recommendation(top3=[sp("Daikin", 12_400_000)], excluded=None, assumptions=[])


PROF = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])


class ExplodingLLM(FakeLLM):
    def stream_text(self, system, user):
        raise RuntimeError("boom")


def test_grounded_lines_are_emitted_live_and_match_message():
    msg = "Dạ em gợi ý:\n- Daikin giá 12.400.000đ, 300 kWh/năm.\nAnh/chị tham khảo ạ."
    llm = FakeLLM(text_responses=[msg])
    emitted = []
    advice, streamed = stream_advice(_reco(), PROF, llm, emitted.append)
    assert streamed is True
    assert "".join(emitted) == msg == advice.message   # live text = final reply text
    assert len(emitted) >= 3                            # per-line, not one blob
    assert is_grounded(advice)


def test_unsourced_number_stops_emission_fail_closed():
    msg = "Dạ em gợi ý:\n- Daikin giá 9.999.999đ cực rẻ.\n- Dòng sau không được phát."
    llm = FakeLLM(text_responses=[msg])
    emitted = []
    advice, streamed = stream_advice(_reco(), PROF, llm, emitted.append)
    assert streamed is False
    assert emitted == ["Dạ em gợi ý:\n"]               # stopped at the bad line
    assert not is_grounded(advice)                      # orchestrator will use safe summary


def test_stream_failure_falls_back_to_blocking_path():
    llm = ExplodingLLM(text_responses=["Daikin giá 12.400.000đ."])
    emitted = []
    advice, streamed = stream_advice(_reco(), PROF, llm, emitted.append)
    assert streamed is False and emitted == []
    assert "12.400.000" in advice.message               # regenerated via complete_text


def test_empty_top3_no_llm_no_emission():
    llm = FakeLLM(text_responses=["should not be used"])
    emitted = []
    advice, streamed = stream_advice(Recommendation(top3=[], excluded=None, assumptions=[]),
                                     PROF, llm, emitted.append)
    assert streamed is False and emitted == []
    assert "chưa tìm được" in advice.message.lower()
