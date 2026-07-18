from app.catalog.crawl_clean import (
    clean_crawl,
    clean_price,
    clean_record,
    clean_specs,
    clean_text,
    name_from_url,
    parse_quantity_sold,
    parse_rating,
)


def _raw(**over):
    base = {
        "time_crawler": "2026-07-17 12:54:19",
        "category_id": 14058,
        "category_name": "Quạt các loại",
        "product_id": "336956",
        "productcode": "0162791000229",
        "producttype": "1",
        "tên sản phẩm": "Quạt cầm tay mini Hydrus JF-102",
        "brand": "Hydrus",
        "url_image": "https://cdn.tgdd.vn/336956.jpg",
        "Giá gốc": 390000.0,
        "Giá khuyến mãi": 235000.0,
        "rating_vote": "4.9",
        "quantity_sold": "14,5k",
        "màu sắc": "Xanh Dương",
        "Phụ kiện đi kèm": "",
        "chính sách bảo hành": "Đổi trả trong 7 ngày.",
        "promotion": "",
        "outstanding": "",
        "spec_product": {"Loại quạt": "Quạt cầm tay", "Hãng": "Hydrus. Xem thông tin hãng"},
        "onlineSaleOnly": False,
        "url": "https://www.dienmayxanh.com/quat-mini/quat-cam-tay-mini-hydrus-jf-102",
    }
    base.update(over)
    return base


# ---- field parsers ----

def test_clean_text_empty_and_whitespace():
    assert clean_text("") is None
    assert clean_text("  ") is None
    assert clean_text(" a  b ") == "a b"
    assert clean_text(None) is None


def test_clean_price_zero_is_missing():
    assert clean_price(0) is None
    assert clean_price(0.0) is None
    assert clean_price("") is None
    assert clean_price(None) is None
    assert clean_price(390000.0) == 390000


def test_parse_rating():
    assert parse_rating("4.9") == 4.9
    assert parse_rating("4") == 4.0
    assert parse_rating("") is None
    assert parse_rating("9.9") is None  # ngoài thang 0-5
    assert parse_rating("abc") is None


def test_parse_quantity_sold_formats():
    assert parse_quantity_sold("292") == 292
    assert parse_quantity_sold("3") == 3
    assert parse_quantity_sold("14,5k") == 14500
    assert parse_quantity_sold("9k") == 9000
    assert parse_quantity_sold("134,8k") == 134800
    assert parse_quantity_sold("1000k+") == 1000000  # cận dưới
    assert parse_quantity_sold("") is None
    assert parse_quantity_sold("n/a") is None


def test_clean_specs_strips_boilerplate_and_empties():
    out = clean_specs({"Hãng": "Hydrus. Xem thông tin hãng", "A": "", "B": " x "})
    assert out == {"Hãng": "Hydrus", "B": "x"}
    assert clean_specs(None) == {}


def test_name_from_url():
    assert name_from_url(
        "https://www.dienmayxanh.com/tai-nghe/tai-nghe-bluetooth-soundcore?utm=1"
    ) == "tai nghe bluetooth soundcore"
    assert name_from_url(None) is None


# ---- record / batch ----

def test_clean_record_happy_path():
    p = clean_record(_raw())
    assert p.name == "Quạt cầm tay mini Hydrus JF-102"
    assert p.name_source == "crawl"
    assert p.original_price == 390000 and p.sale_price == 235000
    assert p.rating == 4.9
    assert p.quantity_sold == 14500
    assert p.quantity_sold_text == "14,5k"
    assert p.accessories is None  # "" -> None
    assert p.specs["Hãng"] == "Hydrus"
    assert p.product_type == 1
    assert p.source == "crawl dienmayxanh"


def test_clean_record_name_fallback_from_url_slug():
    p = clean_record(_raw(**{"tên sản phẩm": ""}))
    assert p.name == "quat cam tay mini hydrus jf 102"
    assert p.name_source == "url_slug"


def test_clean_crawl_dedupes_by_product_id_keeping_latest():
    old = _raw(time_crawler="2026-07-17 10:00:00", rating_vote="4.0")
    new = _raw(time_crawler="2026-07-17 12:00:00", rating_vote="4.9")
    cleaned, report = clean_crawl([old, new])
    assert len(cleaned) == 1
    assert cleaned[0].rating == 4.9
    assert report["dup_product_id_dropped"] == 1
    assert report["total_in"] == 2 and report["total_out"] == 1


def test_clean_crawl_report_counts_anomalies():
    records = [
        _raw(),
        _raw(product_id="2", **{"Giá gốc": 0, "Giá khuyến mãi": 0}),
        _raw(product_id="3", **{"Giá gốc": 100, "Giá khuyến mãi": 200}),
        _raw(product_id="4", quantity_sold="???", rating_vote="", spec_product={}),
    ]
    _, report = clean_crawl(records)
    assert report["no_price"] == 1
    assert report["sale_gt_original"] == 1
    assert report["quantity_sold_unparsed"] == 1
    assert report["no_rating"] == 1
    assert report["no_specs"] == 1
