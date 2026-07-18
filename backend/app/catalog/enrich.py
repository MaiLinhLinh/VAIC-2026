"""Enrichment: chèn thông tin thương mại từ bản crawl đã làm sạch vào catalog.

Chỉ ghép bằng khoá chính xác — productidweb ↔ product_id (chính), sku ↔
productcode (phụ); không so khớp mờ. Mọi trường crawl broadcast cho tất cả
biến thể cùng productidweb. Giá: crawl thắng (mới hơn, có ngày cào); thông số
kỹ thuật luôn giữ theo spec sheet. Dòng không match giữ nguyên "chưa có dữ liệu".
"""
from __future__ import annotations
import json
import os
from collections import Counter

from app.catalog.crawl_clean import SOURCE_CRAWL, clean_crawl
from app.schemas import Product, SourcedValue


def _valid_key(pid) -> str | None:
    """Khoá placeholder (toàn số 9) hoặc không phải số nguyên dương -> không có khoá."""
    s = str(pid).strip() if pid is not None else ""
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit() or set(s) == {"9"}:
        return None
    return s


def load_crawl_records(crawl_cleaned_path: str, crawl_raw_path: str | None = None) -> list[dict]:
    if os.path.exists(crawl_cleaned_path):
        with open(crawl_cleaned_path, encoding="utf-8") as f:
            return json.load(f)
    if crawl_raw_path and os.path.exists(crawl_raw_path):
        with open(crawl_raw_path, encoding="utf-8") as f:
            cleaned, _ = clean_crawl(json.load(f))
        return [p.model_dump() for p in cleaned]
    return []


def enrich_products(products: list[Product], crawl_records: list[dict]) -> Counter:
    report: Counter = Counter()
    by_pid = {str(r["product_id"]): r for r in crawl_records if r.get("product_id")}
    by_code = {str(r["product_code"]): r for r in crawl_records if r.get("product_code")}

    for p in products:
        pid = _valid_key(p.raw.get("productidweb"))
        if pid is None:
            report["khoa_placeholder"] += 1
        c = (by_pid.get(pid) if pid else None) or by_code.get(str(p.sku))
        if c is None:
            continue
        report["matched"] += 1
        as_of = (c.get("crawled_at") or "")[:10] or None

        if c.get("name"):
            p.raw["display_name_tong_hop"] = p.display_name
            p.display_name = c["name"]
            report["ten_tu_crawl"] += 1

        # Giá: crawl thắng và thay thế (quyết định Q5-c) — nhãn nguồn + ngày cào
        orig, sale = c.get("original_price"), c.get("sale_price")
        if orig or sale:
            p.original_price = (SourcedValue.of(int(orig), SOURCE_CRAWL, as_of=as_of)
                                if orig else SourcedValue.missing())
            sale_valid = sale and (not orig or sale <= orig)
            p.sale_price = (SourcedValue.of(int(sale), SOURCE_CRAWL, as_of=as_of)
                            if sale_valid else SourcedValue.missing())
            if sale_valid:
                p.price = SourcedValue.of(int(sale), SOURCE_CRAWL,
                                          detail="giá khuyến mãi", as_of=as_of)
            else:
                p.price = SourcedValue.of(int(orig), SOURCE_CRAWL,
                                          detail="giá gốc", as_of=as_of)
            report["gia_tu_crawl"] += 1

        if c.get("rating") is not None:
            p.rating = SourcedValue.of(c["rating"], SOURCE_CRAWL, unit="⭐/5", as_of=as_of)
            report["rating_tu_crawl"] += 1
        if c.get("quantity_sold") is not None:
            p.quantity_sold = SourcedValue.of(c["quantity_sold"], SOURCE_CRAWL,
                                              unit="lượt", as_of=as_of)
        if c.get("warranty_policy"):
            p.warranty = SourcedValue.of(c["warranty_policy"], SOURCE_CRAWL, as_of=as_of)
        p.url = c.get("url") or p.url
        p.image_url = c.get("image_url") or p.image_url
        p.crawl_promotion = c.get("promotion") or None
    return report
