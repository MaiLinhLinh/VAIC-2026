from app.llm.client import FakeLLM
from app.agent_core.sales import (cross_sell_suggestion, cross_sell_line,
                                  is_order_confirmation, is_aftersales_question)
from app.agent_core.engine import AgentCoreEngine
from tests.agent_helpers import make_db


def _combo_db(tmp_path):
    db = str(tmp_path / "combo.db")
    make_db(db, [
        {"category": "Máy Giặt", "brand": "Toshiba", "model_code": "MG1", "price_clean": 8_000_000,
         "specs": {"Khối lượng giặt": "8 kg", "bảo hành (crawl)": "12 tháng"}},
        {"category": "Máy Giặt", "brand": "LG", "model_code": "MG2", "price_clean": 9_000_000,
         "specs": {"Khối lượng giặt": "9 kg"}},
        {"category": "Máy Sấy Quần Áo", "brand": "Electrolux", "model_code": "MS1",
         "price_clean": 6_000_000, "specs": {"Khối lượng sấy": "7 kg"}},
        {"category": "Đồng Hồ Thông Minh", "brand": "Apple", "model_code": "DH1",
         "price_clean": 5_000_000, "specs": {}},
    ])
    return db


# --- cross-sell (bán chéo) -------------------------------------------------

def test_cross_sell_suggestion_returns_real_complementary_product(tmp_path):
    db = _combo_db(tmp_path)
    row = cross_sell_suggestion("Máy Giặt", 8_000_000, db_path=db, exclude_sku="MG1")
    assert row is not None
    assert row["category"] == "Máy Sấy Quần Áo"
    assert row["model_code"] == "MS1"


def test_cross_sell_suggestion_none_when_no_mapping(tmp_path):
    db = _combo_db(tmp_path)
    assert cross_sell_suggestion("Đồng Hồ Thông Minh", 5_000_000, db_path=db) is None


def test_cross_sell_line_grounded_in_real_name_and_price(tmp_path):
    db = _combo_db(tmp_path)
    row = cross_sell_suggestion("Máy Giặt", 8_000_000, db_path=db)
    line = cross_sell_line(row)
    assert "Electrolux" in line
    assert "6.000.000đ" in line
    # Phải nêu rõ NGÀNH HÀNG (product_display_name chỉ trả hãng+mã, không tự nói là máy gì)
    # để khách không tưởng đây là gợi ý sản phẩm không liên quan.
    assert "Máy Sấy Quần Áo" in line


def test_order_confirmation_keywords():
    assert is_order_confirmation("chốt đơn máy này giúp em")
    assert is_order_confirmation("ok mình lấy máy này luôn")
    assert not is_order_confirmation("máy này bảo hành thế nào")


def test_aftersales_keywords():
    assert is_aftersales_question("máy em đã mua bảo hành bao lâu")
    assert is_aftersales_question("đơn hàng trước em mua rồi giờ hỏi thêm")
    assert not is_aftersales_question("máy này bảo hành bao lâu")


# --- luồng đầy đủ qua graph: chốt đơn -> chăm sóc sau mua ------------------

def _reco_llm():
    return FakeLLM(
        json_responses=[{"category": "Máy Giặt", "budget_max": 10000000, "priority_features": [],
                         "needs_clarification": False, "is_meta_inquiry": False,
                         "clarification_questions": [], "brand": None}],
        text_responses=["Máy Toshiba 8kg giá 8.000.000đ và LG 9kg giá 9.000.000đ ạ."])


def test_confirm_purchase_then_aftersales_flow(tmp_path):
    db = _combo_db(tmp_path)
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=db)

    out = eng.handle("care1", "mua máy giặt dưới 10tr")
    assert out["stage"] == "recommended"

    # Khách chốt máy đầu tiên -> ghi nhận lịch sử mua hàng + gợi ý mua kèm máy sấy.
    out = eng.handle("care1", "chốt đơn máy 1 luôn")
    assert "Toshiba" in out["reply"]
    assert "8.000.000" in out["reply"]
    assert "Electrolux" in out["reply"]  # gợi ý bán chéo máy sấy

    # Sau đó hỏi bảo hành cho máy đã mua -> phải tra đúng warranty của máy đã chốt (12 tháng),
    # không phải máy còn lại trong danh sách đề xuất.
    out = eng.handle("care1", "máy em đã mua bảo hành bao lâu vậy")
    assert "12 tháng" in out["reply"]
    assert "Toshiba" in out["reply"]


def test_aftersales_without_prior_purchase_asks_for_product(tmp_path):
    db = _combo_db(tmp_path)
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=db)
    out = eng.handle("care2", "máy em đã mua trước đó bảo hành sao rồi")
    assert "chưa thấy đơn hàng nào" in out["reply"]
