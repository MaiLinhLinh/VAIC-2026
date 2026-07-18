"""Test enrichment: ghép thông tin thương mại từ bản crawl vào catalog.

Quy tắc (CONTEXT.md): chỉ khoá chính xác (productidweb ↔ product_id,
sku ↔ productcode), khoá placeholder toàn-số-9 / không-phải-số bị bỏ,
giá crawl thắng và mang nhãn nguồn + ngày cào, dòng không match giữ nguyên.
"""
from app.catalog.enrich import _valid_key, enrich_products
from app.schemas import Product, SourcedValue


def mk_product(pid="358160", sku="1751097000147", price=None) -> Product:
    return Product(
        category="Tủ lạnh", category_code="tu_lanh", model_code="1", sku=sku,
        brand="Hitachi", display_name="Tủ lạnh Hitachi 656 lít",
        price=SourcedValue.of(price, "catalog") if price else SourcedValue.missing(),
        original_price=SourcedValue.missing(), sale_price=SourcedValue.missing(),
        raw={"productidweb": pid},
    )


def mk_crawl(pid="358160", **over) -> dict:
    rec = {
        "product_id": pid, "product_code": "1751097000147",
        "crawled_at": "2026-07-17 11:26:24",
        "name": "Tủ lạnh Hitachi Inverter 656 lít Side By Side HRSN9713ESGBKVN",
        "original_price": 29990000, "sale_price": 26990000,
        "rating": 4.9, "quantity_sold": 292,
        "warranty_policy": "Hư gì đổi nấy 12 tháng; Bảo hành chính hãng 2 năm",
        "promotion": "Phiếu mua hàng 300.000đ", "url": "https://x", "image_url": "https://y",
    }
    rec.update(over)
    return rec


def test_valid_key_loai_placeholder_va_cong_thuc():
    assert _valid_key("358160") == "358160"
    assert _valid_key(358160.0) == "358160"      # pandas đọc số thành float
    assert _valid_key("9999") is None            # toàn số 9 = placeholder
    assert _valid_key("99999") is None
    assert _valid_key("=VLOOKUP(B2,...)") is None  # công thức Excel chưa tính
    assert _valid_key(None) is None


def test_enrich_dong_match_gia_crawl_thang_va_co_nhan_nguon():
    p = mk_product(price=25_000_000)
    report = enrich_products([p], [mk_crawl()])
    assert report["matched"] == 1
    # giá crawl thay thế, nhãn nguồn + ngày cào (quyết định Q5-c)
    assert p.price.value == 26_990_000
    assert p.price.provenance.source == "crawl dienmayxanh"
    assert p.price.provenance.as_of == "2026-07-17"
    assert p.original_price.value == 29_990_000
    # tên thật thay tên tổng hợp, bản tổng hợp giữ trong raw
    assert "HRSN9713ESGBKVN" in p.display_name
    assert p.raw["display_name_tong_hop"] == "Tủ lạnh Hitachi 656 lít"
    assert p.rating.value == 4.9 and p.quantity_sold.value == 292
    assert p.warranty.available and p.crawl_promotion == "Phiếu mua hàng 300.000đ"


def test_enrich_khong_match_giu_nguyen_chua_co_du_lieu():
    p = mk_product(pid="111", sku="khac")
    report = enrich_products([p], [mk_crawl(pid="222", product_code="333")])
    assert report.get("matched", 0) == 0
    assert p.price.available is False
    assert p.rating.available is False
    assert p.display_name == "Tủ lạnh Hitachi 656 lít"


def test_enrich_khoa_placeholder_khong_ghep_nham():
    # id 9999 tồn tại trong crawl nhưng là placeholder trong xlsx -> không ghép qua pid
    p = mk_product(pid="9999", sku="sku-khong-khop")
    report = enrich_products([p], [mk_crawl(pid="9999")])
    assert report["khoa_placeholder"] == 1
    assert p.price.available is False


def test_enrich_fallback_khoa_phu_sku():
    p = mk_product(pid="=VLOOKUP(B2,...)", sku="1751097000147")
    report = enrich_products([p], [mk_crawl(pid="khac-han")])
    assert report["matched"] == 1
    assert p.price.value == 26_990_000


def test_enrich_sale_lon_hon_goc_khong_lay_lam_gia_hieu_luc():
    p = mk_product()
    enrich_products([p], [mk_crawl(original_price=10_000_000, sale_price=12_000_000)])
    assert p.price.value == 10_000_000            # sale > gốc: bất thường, dùng giá gốc
    assert p.sale_price.available is False


def test_enrich_broadcast_bien_the_cung_productidweb():
    a, b = mk_product(sku="sku-a"), mk_product(sku="sku-b")
    report = enrich_products([a, b], [mk_crawl()])
    assert report["matched"] == 2
    assert a.price.value == b.price.value == 26_990_000
