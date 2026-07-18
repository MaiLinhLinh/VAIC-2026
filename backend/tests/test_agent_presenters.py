from app.agent_core.presenters import (product_display_name, parse_leading_number,
                                        load_specs, build_reco_card, build_detail_card)


def _row(**kw):
    base = {"model_code": "TL1", "sku": "S1", "category": "Tủ Lạnh", "brand": "Toshiba",
            "price_clean": 12_400_000, "gift_promo": "", "key_specs_summary": "",
            "full_specs_json": '{"Dung tích tổng": "300 lít", "Điện năng tiêu thụ": "350 kWh/năm"}'}
    base.update(kw)
    return base


def test_display_name_uses_brand_and_code():
    assert "Toshiba" in product_display_name(_row())
    assert "TL1" in product_display_name(_row())


def test_parse_leading_number():
    assert parse_leading_number("300 lít") == 300.0
    assert parse_leading_number("1,3 kg") == 1.3
    assert parse_leading_number("không") is None


def test_load_specs_parses_json():
    specs = load_specs(_row())
    assert specs["Dung tích tổng"] == "300 lít"


def test_reco_card_has_price_and_missing():
    card = build_reco_card(_row(), ["tiết kiệm điện"])
    assert card.title.startswith("Vì sao em đề xuất")
    labels = [l.label for l in card.lines]
    assert "Giá" in labels and "Thương hiệu" in labels
    assert "tồn kho" in card.missing
    price_line = next(l for l in card.lines if l.label == "Giá")
    assert "12.400.000" in price_line.value


def test_reco_card_missing_price():
    card = build_reco_card(_row(price_clean=0), [])
    assert "giá" in card.missing


def test_detail_card_lists_all_specs():
    card = build_detail_card(_row())
    labels = [l.label for l in card.lines]
    assert "Dung tích tổng" in labels and "Điện năng tiêu thụ" in labels
