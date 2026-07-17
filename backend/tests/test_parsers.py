import math
from app.catalog.parsers import (
    parse_number, parse_range, parse_measure, parse_bool, parse_people, resolve_price,
)


def test_parse_number_units_and_types():
    assert parse_number("313 lít") == 313
    assert parse_number("1.3 inch") == 1.3
    assert parse_number("300 cd/m2") == 300
    assert parse_number("170") == 170
    assert parse_number(84.0) == 84.0
    for junk in ["Không", "Không có", "", None, float("nan")]:
        assert parse_number(junk) is None


def test_parse_range():
    assert parse_range("1720W - 2050W") == (1720.0, 2050.0)
    assert parse_range("4 ~ 14 lít/lần rửa") == (4.0, 14.0)
    assert parse_range("3 - 4 người") == (3.0, 4.0)
    assert parse_range("313 lít") == (313.0, 313.0)
    assert parse_range("Không") is None


def test_parse_measure_uses_midpoint():
    assert parse_measure("1720W - 2050W") == 1885.0
    assert parse_measure("46 dB") == 46.0


def test_parse_bool():
    assert parse_bool("Có") is True
    assert parse_bool("Không") is False
    assert parse_bool("Không có") is False
    assert parse_bool("Không cảm ứng") is False
    assert parse_bool(float("nan")) is None


def test_parse_people():
    assert parse_people("3 - 4 người") == (3, 4)
    assert parse_people("1 người") == (1, 1)
    assert parse_people("Không") is None


def test_resolve_price():
    price, orig, sale = resolve_price(14990000.0, float("nan"))
    assert price.value == 14990000 and sale.available is False
    price, orig, sale = resolve_price(19990000.0, 14990000.0)
    assert price.value == 14990000 and sale.value == 14990000 and orig.value == 19990000
    price, orig, sale = resolve_price(float("nan"), float("nan"))
    assert price.available is False and price.note == "chưa có dữ liệu"
    # sale > orig là bất thường -> bỏ sale, dùng orig
    price, orig, sale = resolve_price(10000000.0, 12000000.0)
    assert price.value == 10000000 and sale.available is False


def test_resolve_price_zero_original_not_treated_as_missing():
    price, orig, sale = resolve_price(0.0, float("nan"))
    assert orig.available is True and orig.value == 0
    assert price.available is True and price.value == 0
