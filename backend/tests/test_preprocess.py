from app.nlu.preprocess import strip_accents, expand_shorthand, parse_budget_vnd, detect_category


def test_strip_accents():
    assert strip_accents("Tủ Lạnh tiết kiệm điện") == "Tu Lanh tiet kiem dien"


def test_expand_shorthand():
    out = expand_shorthand("mua may lanh 20tr cho phong 18m2")
    assert "20 triệu" in out and "18 m²" in out


def test_parse_budget_vnd():
    assert parse_budget_vnd("dưới 20 triệu") == (None, 20_000_000)
    assert parse_budget_vnd("trên 5 triệu") == (5_000_000, None)
    assert parse_budget_vnd("khoảng 10-15 triệu") == (10_000_000, 15_000_000)
    assert parse_budget_vnd("12tr") == (None, 12_000_000)
    assert parse_budget_vnd("500k") == (None, 500_000)


def test_detect_category_no_accents():
    assert detect_category("e muon mua tu lanh") == "tu_lanh"
    assert detect_category("cần cái đồng hồ thông minh") == "dong_ho"
    assert detect_category("mua màn hình gaming") == "man_hinh"
    assert detect_category("xin chào") is None
