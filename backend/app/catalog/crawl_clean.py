"""Làm sạch dữ liệu crawl dienmayxanh (data/products_detail.json).

Nguồn "thương mại" bù cho spec sheet: giá (phủ ~99,9%), rating, lượt bán,
chính sách bảo hành, promotion. Mọi giá trị thiếu là None tường minh —
không suy đoán; các bất thường được đếm vào báo cáo thay vì sửa ngầm.
"""
from __future__ import annotations
import html
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

SOURCE_CRAWL = "crawl dienmayxanh"

_QTY = re.compile(r"^(\d+(?:,\d+)?)(k?)(\+?)$")
_SPEC_BOILERPLATE = re.compile(r"\.?\s*Xem thông tin hãng\s*$")
_INVISIBLE = re.compile(r"[​‌‍‎‏﻿]")


class CrawlProduct(BaseModel):
    source: str = SOURCE_CRAWL
    crawled_at: str
    category_id: int
    category_name: str
    product_id: str
    product_code: str | None = None
    product_type: int | None = None
    name: str | None = None
    name_source: str | None = None  # "crawl" | "url_slug"
    brand: str | None = None
    url: str | None = None
    image_url: str | None = None
    original_price: int | None = None
    sale_price: int | None = None
    rating: float | None = None
    quantity_sold: int | None = None
    quantity_sold_text: str | None = None
    color: str | None = None
    accessories: str | None = None
    warranty_policy: str | None = None
    promotion: str | None = None
    outstanding: str | None = None
    online_sale_only: bool = False
    specs: dict[str, str] = Field(default_factory=dict)


def clean_text(v) -> str | None:
    if v is None:
        return None
    t = html.unescape(str(v))               # "ho&#xe0; ph&#xe1;t" -> "hoà phát"
    t = _INVISIBLE.sub("", t).replace(" ", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def clean_price(v) -> int | None:
    """Giá 0 hoặc trống nghĩa là chưa có dữ liệu, không phải miễn phí."""
    if v is None or v == "":
        return None
    try:
        p = int(float(v))
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def parse_rating(v) -> float | None:
    t = clean_text(v)
    if t is None:
        return None
    try:
        r = float(t)
    except ValueError:
        return None
    return r if 0 <= r <= 5 else None


def parse_quantity_sold(v) -> int | None:
    """'292' -> 292, '14,5k' -> 14500, '1000k+' -> 1000000 (cận dưới)."""
    t = clean_text(v)
    if t is None:
        return None
    m = _QTY.match(t.replace(".", ","))
    if not m:
        return None
    num, kilo, _plus = m.groups()
    val = float(num.replace(",", "."))
    if kilo:
        val *= 1000
    return int(val)


def clean_specs(raw: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (raw or {}).items():
        key = clean_text(k)
        val = clean_text(v)
        if key is None or val is None:
            continue
        val = _SPEC_BOILERPLATE.sub("", val).strip()
        if val:
            out[key] = val
    return out


def name_from_url(url: str | None) -> str | None:
    """Fallback khi crawl thiếu tên: dựng từ slug URL của chính sản phẩm."""
    if not url:
        return None
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    name = clean_text(slug.replace("-", " "))
    return name


def clean_record(raw: dict[str, Any]) -> CrawlProduct:
    name = clean_text(raw.get("tên sản phẩm"))
    name_source = "crawl" if name else None
    if name is None:
        name = name_from_url(raw.get("url"))
        name_source = "url_slug" if name else None

    original = clean_price(raw.get("Giá gốc"))
    sale = clean_price(raw.get("Giá khuyến mãi"))

    ptype = clean_text(raw.get("producttype"))
    return CrawlProduct(
        crawled_at=clean_text(raw.get("time_crawler")) or "",
        category_id=int(raw.get("category_id")),
        category_name=clean_text(raw.get("category_name")) or "",
        product_id=str(raw.get("product_id")),
        product_code=clean_text(raw.get("productcode")),
        product_type=int(ptype) if ptype and ptype.isdigit() else None,
        name=name,
        name_source=name_source,
        brand=clean_text(raw.get("brand")),
        url=clean_text(raw.get("url")),
        image_url=clean_text(raw.get("url_image")),
        original_price=original,
        sale_price=sale,
        rating=parse_rating(raw.get("rating_vote")),
        quantity_sold=parse_quantity_sold(raw.get("quantity_sold")),
        quantity_sold_text=clean_text(raw.get("quantity_sold")),
        color=clean_text(raw.get("màu sắc")),
        accessories=clean_text(raw.get("Phụ kiện đi kèm")),
        warranty_policy=clean_text(raw.get("chính sách bảo hành")),
        promotion=clean_text(raw.get("promotion")),
        outstanding=clean_text(raw.get("outstanding")),
        online_sale_only=bool(raw.get("onlineSaleOnly")),
        specs=clean_specs(raw.get("spec_product")),
    )


def clean_crawl(records: list[dict]) -> tuple[list[CrawlProduct], dict]:
    """Làm sạch toàn bộ; trùng product_id giữ bản crawl mới nhất."""
    report: Counter = Counter(total_in=len(records))
    by_id: dict[str, CrawlProduct] = {}
    for raw in records:
        p = clean_record(raw)
        prev = by_id.get(p.product_id)
        if prev is not None:
            report["dup_product_id_dropped"] += 1
            if p.crawled_at <= prev.crawled_at:
                continue
        by_id[p.product_id] = p

    cleaned = list(by_id.values())
    for p in cleaned:
        if p.original_price is None and p.sale_price is None:
            report["no_price"] += 1
        if (p.original_price is not None and p.sale_price is not None
                and p.sale_price > p.original_price):
            report["sale_gt_original"] += 1
        if p.name is None:
            report["no_name"] += 1
        elif p.name_source == "url_slug":
            report["name_from_url_slug"] += 1
        if p.rating is None:
            report["no_rating"] += 1
        if p.quantity_sold is None and p.quantity_sold_text is not None:
            report["quantity_sold_unparsed"] += 1
        if not p.specs:
            report["no_specs"] += 1
    report["total_out"] = len(cleaned)
    return cleaned, dict(report)
