from app.llm.client import FakeLLM
from app.agent_core.engine import AgentCoreEngine
from tests.agent_helpers import make_db


def _db(tmp_path):
    db = str(tmp_path / "g.db")
    make_db(db, [
        {"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL1", "price_clean": 12_400_000,
         "specs": {"Dung tích tổng": "300 lít", "Điện năng tiêu thụ": "350 kWh/năm"}},
        {"category": "Tủ Lạnh", "brand": "LG", "model_code": "TL2", "price_clean": 11_000_000,
         "specs": {"Dung tích tổng": "250 lít", "Điện năng tiêu thụ": "300 kWh/năm"}},
        {"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL3", "price_clean": 13_000_000,
         "specs": {"Dung tích tổng": "350 lít", "Điện năng tiêu thụ": "370 kWh/năm"}},
    ])
    return db


def _reco_llm():
    return FakeLLM(
        json_responses=[{"category": "Tủ Lạnh", "budget_max": 20000000, "priority_features": ["tiết kiệm điện"],
                         "needs_clarification": False, "is_meta_inquiry": False,
                         "clarification_questions": [], "brand": None}],
        text_responses=["Máy Toshiba giá 12.400.000đ và LG giá 11.000.000đ, cả hai tiết kiệm điện tốt."])


def test_recommend_turn_shape(tmp_path):
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=_db(tmp_path))
    out = eng.handle("s1", "mua tủ lạnh dưới 20tr tiết kiệm điện")
    assert out["stage"] == "recommended"
    assert out["recommendation"] is not None
    assert len(out["recommendation"]["cards"]) >= 2
    assert out["recommendation"]["comparison"] is not None
    assert "12.400.000" in out["reply"]


def test_clarify_turn(tmp_path):
    llm = FakeLLM(json_responses=[{"category": None, "budget_max": None, "brand": None,
                                   "priority_features": [], "needs_clarification": True,
                                   "is_meta_inquiry": False,
                                   "clarification_questions": ["Bạn cần nhóm sản phẩm nào ạ?"]}])
    eng = AgentCoreEngine(llm=llm, db_path=_db(tmp_path))
    out = eng.handle("s2", "tư vấn giúp em")
    assert out["stage"] == "collecting"
    assert out["recommendation"] is None
    assert "?" in out["reply"]


def test_detail_followup_uses_memory(tmp_path):
    db = _db(tmp_path)
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=db)
    eng.handle("s3", "mua tủ lạnh dưới 20tr tiết kiệm điện")   # tạo last_products
    eng.llm = FakeLLM(json_responses=[{"category": "Tủ Lạnh", "needs_clarification": False,
                                       "is_meta_inquiry": False, "priority_features": [],
                                       "clarification_questions": [], "brand": None, "budget_max": None}],
                      text_responses=["Dạ máy Toshiba dung tích 300 lít ạ."])
    out = eng.handle("s3", "máy 1 dung tích bao nhiêu")
    assert "300" in out["reply"]
    assert out["recommendation"]["cards"][0]["title"].startswith("Thông tin chi tiết")


def test_compare_brand_followup_reuses_last_products(tmp_path):
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=_db(tmp_path))
    eng.handle("brand-compare", "mua tủ lạnh dưới 20tr tiết kiệm điện")
    eng.llm = FakeLLM(
        json_responses=[{"category": "Máy tính để bàn", "budget_max": None,
                         "priority_features": [], "needs_clarification": True,
                         "is_meta_inquiry": False, "clarification_questions": [], "brand": None}],
        text_responses=["Dạ em so sánh hai mẫu Toshiba trong danh sách vừa xem ạ."],
    )

    out = eng.handle("brand-compare", "so sánh Toshiba")

    assert out["stage"] == "recommended"
    assert out["need"]["category"] == "Tủ Lạnh"
    assert len(out["recommendation"]["cards"]) == 2
    assert all("Toshiba" in card["title"] for card in out["recommendation"]["cards"])
    assert len(out["recommendation"]["comparison"]["products"]) == 2
    assert out["trace"][1]["data"]["comparison_followup"] is True


def test_reset_clears_memory(tmp_path):
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=_db(tmp_path))
    eng.handle("s4", "mua tủ lạnh dưới 20tr tiết kiệm điện")
    eng.reset("s4")
    eng.llm = FakeLLM(json_responses=[{"category": None, "needs_clarification": True,
                                       "is_meta_inquiry": False, "priority_features": [],
                                       "clarification_questions": ["Bạn cần gì ạ?"],
                                       "brand": None, "budget_max": None}])
    out = eng.handle("s4", "máy 1 thế nào")
    assert out["stage"] == "collecting"
