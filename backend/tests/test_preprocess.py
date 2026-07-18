import pytest

from app.nlu.preprocess import (
    declined_clarification,
    detect_category,
    expand_shorthand,
    extract_explicit_demographics,
    parse_budget_vnd,
    parse_people_count,
    parse_screen_size_inches,
    prefers_large_screen,
    strip_accents,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("mua tu lanh", "tu_lanh"),
        ("cần máy sấy quần áo", "may_say"),
        ("tìm máy rửa chén", "may_rua_chen"),
        ("mua tủ đông", "tu_mat"),
        ("cần đồng hồ thông minh", "dong_ho"),
        ("mua màn hình gaming", "man_hinh"),
    ],
)
def test_detects_every_supported_category(message, expected):
    assert detect_category(message) == expected


def test_strip_accents():
    assert strip_accents("Tủ Lạnh tiết kiệm điện") == "Tu Lanh tiet kiem dien"


def test_expand_shorthand():
    out = expand_shorthand("mua may lanh 20tr cho phong 18m2")
    assert "20 triệu" in out and "18 m²" in out
    assert expand_shorthand("ngân sách 15 củ") == "ngân sách 15 triệu"


def test_parse_budget_vnd():
    assert parse_budget_vnd("dưới 20 triệu") == (None, 20_000_000)
    assert parse_budget_vnd("trên 5 triệu") == (5_000_000, None)
    assert parse_budget_vnd("khoảng 10-15 triệu") == (10_000_000, 15_000_000)
    assert parse_budget_vnd("12tr") == (None, 12_000_000)
    assert parse_budget_vnd("500k") == (None, 500_000)
    assert parse_budget_vnd("khoảng 30tr quay đầu") == (None, 30_000_000)
    assert parse_budget_vnd("từ 15 triệu trở xuống") == (None, 15_000_000)
    assert parse_budget_vnd("ít nhất 10 triệu") == (10_000_000, None)
    assert parse_budget_vnd("tủ lạnh tầm 13 triệu") == (None, 13_000_000)
    assert parse_budget_vnd("ngân sách 10 triệu trở lên") == (10_000_000, None)


def test_detect_category_no_accents():
    assert detect_category("e muon mua tu lanh") == "tu_lanh"
    assert detect_category("cần cái đồng hồ thông minh") == "dong_ho"
    assert detect_category("mua màn hình gaming") == "man_hinh"
    assert detect_category("xin chào") is None


def test_parse_people_count_with_accents_shorthand_and_words():
    assert parse_people_count("nhà 4 người") == (4, 4)
    assert parse_people_count("gia dinh bon nguoi") == (4, 4)
    assert parse_people_count("dùng cho 3-5 thành viên") == (3, 5)
    assert parse_people_count("khoảng mười hai người") == (12, 12)
    assert parse_people_count("4", allow_bare=True) == (4, 4)
    assert parse_people_count("bốn", allow_bare=True) == (4, 4)
    assert parse_people_count("4") is None


def test_parse_people_count_ignores_unrelated_numbers():
    assert parse_people_count("tủ lạnh 4 cửa, bảo hành 2 năm") is None
    assert parse_people_count("nhà mình mấy người thì phù hợp?") is None
    assert parse_people_count("không phải 4 người, nhà mình 5 người") == (5, 5)
    assert parse_people_count("3 5 người") is None


def test_parse_screen_size_inches():
    assert parse_screen_size_inches("màn 24 inch") == 24
    assert parse_screen_size_inches("tối thiểu 15 inch") == (15, None)
    assert parse_screen_size_inches("15 inch trở lên") == (15, None)
    assert parse_screen_size_inches("dưới 27 inch") == (None, 27)


@pytest.mark.parametrize("message", ["càng to càng tốt", "càng lớn càng tốt", "màn hình lớn nhất"])
def test_detects_large_screen_preference(message):
    assert prefers_large_screen(message) is True


def test_extract_explicit_demographics():
    assert extract_explicit_demographics("mua dong ho cho be trai 8 tuoi") == {
        "độ tuổi": "8 tuổi",
        "đối tượng": "trẻ em",
        "giới tính": "nam",
    }
    assert extract_explicit_demographics("toi la nu, lam giao vien") == {
        "giới tính": "nữ",
        "nghề nghiệp": "giáo viên",
    }


def test_demographics_are_not_inferred_from_address_or_preferences():
    assert extract_explicit_demographics("chị tư vấn màn hình chơi game giúp em") == {}
    assert extract_explicit_demographics("mình cần loại dễ dùng, chữ lớn") == {}
    assert extract_explicit_demographics("không phải cho trẻ em, cho người lớn") == {
        "đối tượng": "người lớn",
    }


@pytest.mark.parametrize(
    "message",
    [
        "mình không biết nữa, bạn cứ gợi ý đi",
        "mình cũng ko bt nữa bạn cứ cho mình tham khảo mẫu với giá tiền đó nhé",
        "mẫu nào cũng được",
        "tùy em tư vấn",
    ],
)
def test_detects_natural_clarification_declines(message):
    assert declined_clarification(message) is True


def test_decline_detection_does_not_treat_product_uncertainty_as_refusal():
    assert declined_clarification("không biết mẫu này có tiết kiệm điện không") is False
