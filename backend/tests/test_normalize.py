from app.catalog.normalize import normalize_row
from app.catalog.category_config import config_for


def test_normalize_tu_lanh_row():
    cfg = config_for("tu_lanh")
    row = {
        "model_code": 165156, "sku": 1751097000147, "brand": "Samsung",
        "Kiểu dáng": "Ngăn đá dưới", "Dung tích tổng": "313 lít",
        "Điện năng tiêu thụ": "381", "Số người sử dụng": "3 - 4 người",
        "Công nghệ tiết kiệm điện": "Digital Inverter",
        "Công nghệ làm lạnh": "All-around Cooling", "Tiện ích": "Auto Ice Maker | Đèn LED",
        "Công nghệ bảo quản thực phẩm": "Optimal Fresh",
        "giá gốc": 14990000.0, "giá khuyến mãi": float("nan"),
        "khuyến mãi quà": "Miễn phí công lắp đặt",
    }
    p = normalize_row(row, cfg)
    assert p.category_code == "tu_lanh"
    assert p.number("Dung tích tổng") == 313
    assert p.number("Điện năng tiêu thụ") == 381
    assert p.specs["Số người sử dụng"].value == [3, 4]
    assert p.price.value == 14990000 and p.sale_price.available is False
    assert "Samsung" in p.display_name and "313" in p.display_name
    assert "Inverter" in p.spec_doc  # spec_doc gộp field text
    assert p.promo_text == "Miễn phí công lắp đặt"


def test_normalize_missing_price_marks_unavailable():
    cfg = config_for("may_rua_chen")
    row = {"model_code": 1, "sku": 2, "brand": "Bosch", "Loại sản phẩm": "Độc lập",
           "Độ ồn": "46 dB", "giá gốc": float("nan"), "giá khuyến mãi": float("nan")}
    p = normalize_row(row, cfg)
    assert p.price.available is False and p.price.note == "chưa có dữ liệu"
    assert p.number("Độ ồn") == 46


def test_display_name_skips_negatives_and_multivalue():
    cfg = config_for("tu_lanh")
    row = {"model_code": 1, "sku": 2, "brand": "Aqua",
           "Công nghệ tiết kiệm điện": "Không có", "Dung tích tổng": "53 lít",
           "giá gốc": 5_000_000.0, "giá khuyến mãi": float("nan")}
    p = normalize_row(row, cfg)
    assert "Không" not in p.display_name
    assert "Aqua" in p.display_name and "53" in p.display_name
