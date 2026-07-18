"""Xuất bản xlsx sạch + enriched: backend/data/Spec_cate_gia.cleaned.xlsx.

File gốc data/Spec_cate_gia.xlsx không bị đụng tới. Mỗi sheet giữ nguyên tên và
thứ tự cột gốc (đã làm sạch: placeholder -> ô trống, cột rác bị loại, giá trị
ngoài cửa sổ hợp lý -> trống + cột "... (nguyên văn)"), sau đó là các cột dẫn
xuất từ làm sạch, cuối cùng là khối cột enrichment "(crawl)".
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import Workbook

from app.catalog.category_config import CATEGORY_CONFIGS
from app.catalog.enrich import enrich_products, load_crawl_records
from app.catalog.normalize import build_catalog_with_report
from app.config import get_settings

_CRAWL_COLS = [
    "tên sản phẩm", "giá hiệu lực", "nguồn giá",
    "rating", "lượt bán", "bảo hành", "khuyến mãi", "url", "ảnh",
]


def _crawl_values(p) -> dict:
    prov = p.price.provenance
    src = None
    if p.price.available and prov:
        src = prov.source + (f" ({prov.detail})" if prov.detail else "")
        if prov.as_of:
            src += f", ngày {prov.as_of}"
    return {
        "tên sản phẩm": p.display_name if "display_name_tong_hop" in p.raw else None,
        "giá hiệu lực": p.price.value if p.price.available else None,
        "nguồn giá": src,
        "rating": p.rating.value if p.rating.available else None,
        "lượt bán": p.quantity_sold.value if p.quantity_sold.available else None,
        "bảo hành": p.warranty.value if p.warranty.available else None,
        "khuyến mãi": p.crawl_promotion,
        "url": p.url,
        "ảnh": p.image_url,
    }


def main():
    s = get_settings()
    products, _ = build_catalog_with_report(s.dataset_path)
    crawl_records = load_crawl_records(s.crawl_cleaned_path, s.crawl_path)
    if crawl_records:
        enrich_products(products, crawl_records)

    by_code: dict[str, list] = {}
    for p in products:
        by_code.setdefault(p.category_code, []).append(p)

    wb = Workbook()
    wb.remove(wb.active)
    for cfg in CATEGORY_CONFIGS.values():
        ws = wb.create_sheet(title=cfg.sheet_name)
        rows = by_code.get(cfg.code, [])
        # thứ tự cột: theo dòng gốc, cột dẫn xuất mới gặp nối vào sau
        headers: list[str] = []
        for p in rows:
            for k in p.raw:
                if k not in headers and k != "display_name_tong_hop":
                    headers.append(k)
        headers += _CRAWL_COLS
        ws.append(headers)
        for p in rows:
            extra = _crawl_values(p)
            ws.append([p.raw.get(h) if h not in extra else extra[h] for h in headers])

    out = os.path.join(os.path.dirname(s.catalog_path), "Spec_cate_gia.cleaned.xlsx")
    wb.save(out)
    total = sum(len(v) for v in by_code.values())
    print(f"Da xuat {total} dong / {len(wb.sheetnames)} sheet -> {out}")


if __name__ == "__main__":
    main()
