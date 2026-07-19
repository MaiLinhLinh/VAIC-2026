"""Mô tả tìm kiếm đã phi chuẩn hoá cho catalog sản phẩm.

Interface của module chỉ gồm ba việc: chọn cột mô tả, dựng mô tả cho một sản phẩm,
và backfill schema cũ. Mọi luật loại ID/giá/khuyến mãi/cột vận hành nằm tại đây để
ingestion và retrieval không phải biết lại các chi tiết đó.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import json
import logging
import sqlite3
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


log = logging.getLogger("agent_core")

DESCRIPTION_COLUMN = "search_description"
MIN_FIELD_COVERAGE = 0.25
MAX_DESCRIPTION_FIELDS = 16

_EXCLUDED_EXACT = {
    "id", "model code", "sku", "brand", "productidweb", "product id", "product code",
    "category code", "category table", "brand id", "gia goc", "gia khuyen mai",
    "gia hieu luc", "nguon gia", "price orig", "price promo", "price clean",
    "price promo clean", "capacity clean", "khuyen mai qua", "gift promo",
    "promotion", "url", "image url", "url image", "source", "crawled at",
    "time crawler", "online sale only", "quantity sold", "quantity sold text",
    "rating", "rating vote",
    "search description",
}
_EXCLUDED_PARTS = (
    "khuyen mai", "gia ", " price", "productid", "product id", " ma id",
    "url", "image", "crawl", "nguon du lieu", "thoi gian cao",
)
_PRIORITY_GROUPS = (
    ("loai san pham", "chuc nang", "muc dich", "doi tuong", "so nguoi", "nhu cau",
     "cong nghe", "tien ich", "tinh nang", "kieu dang"),
    ("thuong hieu", "ket noi", "dung tich", "khoi luong", "cong suat", "toc do",
     "kich thuoc", "man hinh", "do phan giai", "bo nho", "pin", "do on"),
)


def _flat(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Mn").split())


def _has_value(value: Any) -> bool:
    return _flat(value) not in {"", "nan", "none", "null", "hang khong cong bo"}


def is_description_field(field: str) -> bool:
    """True khi field là thông tin sản phẩm hữu ích cho tìm kiếm tự nhiên."""
    name = _flat(field).replace("_", " ")
    if name in _EXCLUDED_EXACT:
        return False
    return not any(part in f" {name} " for part in _EXCLUDED_PARTS)


def _field_priority(field: str) -> tuple[int, str]:
    name = _flat(field)
    for rank, group in enumerate(_PRIORITY_GROUPS):
        if any(token in name for token in group):
            return rank, name
    return len(_PRIORITY_GROUPS), name


def order_description_fields(fields: Iterable[str]) -> list[str]:
    """Xếp tên cột theo giá trị tư vấn, không cần đọc dữ liệu sản phẩm."""
    return sorted(dict.fromkeys(fields), key=_field_priority)


def select_description_fields(
    specs_rows: Iterable[Mapping[str, Any]],
    *,
    min_coverage: float = MIN_FIELD_COVERAGE,
    max_fields: int = MAX_DESCRIPTION_FIELDS,
) -> list[str]:
    """Chọn các cột có ý nghĩa và đủ dữ liệu, ưu tiên loại/tính năng/công nghệ."""
    rows = list(specs_rows)
    if not rows:
        return []
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(field for field, value in row.items()
                      if is_description_field(str(field)) and _has_value(value))
    eligible = [field for field, count in counts.items()
                if count / len(rows) >= min_coverage]
    eligible.sort(key=lambda field: (_field_priority(field)[0], -counts[field],
                                     _field_priority(field)[1]))
    return eligible[:max_fields]


@lru_cache(maxsize=64)
def description_fields_for_table(db_path: str, table: str) -> tuple[str, ...]:
    """Các cột thực sự đi vào mô tả của một bảng ngành, cache theo DB/table."""
    safe_table = str(table).replace('"', '""')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{safe_table}"')]
    finally:
        conn.close()
    return tuple(select_description_fields(rows))


def build_search_description(
    category: str,
    brand: str,
    specs: Mapping[str, Any],
    fields: Sequence[str] | None = None,
) -> str:
    """Dựng một chuỗi tự đủ ngữ cảnh, không chứa ID/giá/khuyến mãi/vận hành."""
    selected = list(fields) if fields is not None else [
        key for key in specs if is_description_field(str(key))
    ]
    parts = []
    if _has_value(category):
        parts.append(f"Nhóm sản phẩm: {str(category).strip()}")
    if _has_value(brand):
        parts.append(f"Nhãn hàng: {str(brand).strip()}")
    for field in selected:
        value = specs.get(field)
        if _has_value(value):
            parts.append(f"{field}: {str(value).strip()}")
    return "; ".join(parts)


def ensure_search_descriptions(db_path: str) -> int:
    """Backfill DB cũ tại chỗ; DB mới do ingestion tạo sẵn nên đường này gần như O(1)."""
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(all_products)")}
        if DESCRIPTION_COLUMN not in columns:
            conn.execute(f"ALTER TABLE all_products ADD COLUMN {DESCRIPTION_COLUMN} TEXT")
        total, ready = conn.execute(
            f"SELECT COUNT(*), COUNT(NULLIF(TRIM({DESCRIPTION_COLUMN}), '')) FROM all_products"
        ).fetchone()
        if total and ready == total:
            return 0

        rows = conn.execute(
            "SELECT id, category, brand, full_specs_json FROM all_products"
        ).fetchall()
        parsed: list[tuple[int, str, str, dict[str, Any]]] = []
        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row_id, category, brand, raw_specs in rows:
            try:
                specs = json.loads(raw_specs or "{}")
            except (TypeError, ValueError):
                specs = {}
            if not isinstance(specs, dict):
                specs = {}
            category = str(category or "")
            parsed.append((row_id, category, str(brand or ""), specs))
            by_category[category].append(specs)
        fields_by_category = {
            category: select_description_fields(group)
            for category, group in by_category.items()
        }
        updates = [
            (build_search_description(category, brand, specs,
                                      fields_by_category.get(category, [])), row_id)
            for row_id, category, brand, specs in parsed
        ]
        conn.executemany(
            f"UPDATE all_products SET {DESCRIPTION_COLUMN} = ? WHERE id = ?", updates
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_all_products_category_price "
            "ON all_products(category, price_clean)"
        )
        conn.commit()
        log.info("search_description: backfill %d sản phẩm", len(updates))
        return len(updates)
    finally:
        conn.close()
