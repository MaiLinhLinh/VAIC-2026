from __future__ import annotations
import math
import re
import pandas as pd
from app.schemas import Product, SourcedValue
from app.catalog.parsers import parse_number, parse_measure, parse_bool, parse_people, resolve_price
from app.catalog.category_config import CategoryConfig, CATEGORY_CONFIGS

_SRC_SPEC = "thông số nhà sản xuất"


def _is_nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _text(x) -> str | None:
    if _is_nan(x):
        return None
    t = str(x).strip()
    return t or None


def _spec_value(raw, kind: str, unit: str | None) -> SourcedValue:
    if kind == "number":
        v = parse_number(raw)
    elif kind == "range":
        v = parse_measure(raw)
    elif kind == "people":
        pr = parse_people(raw)
        v = list(pr) if pr else None
    elif kind == "bool":
        v = parse_bool(raw)
    else:  # text, multi
        v = _text(raw)
    if v is None:
        return SourcedValue.missing()
    return SourcedValue.of(v, _SRC_SPEC, unit=unit)


def _build_name(template: str, row: dict, brand: str) -> str:
    def repl(m):
        key = m.group(1)
        if key == "brand":
            return brand
        t = _text(row.get(key))
        if t is None or t.lower().startswith("không"):
            return ""
        return t.split("|")[0].strip()
    name = re.sub(r"\{([^}]+)\}", repl, template)
    return re.sub(r"\s+", " ", name).strip()


def normalize_row(row: dict, cfg: CategoryConfig) -> Product:
    brand = _text(row.get("brand")) or "?"
    specs: dict[str, SourcedValue] = {}
    for sd in cfg.specs:
        specs[sd.field] = _spec_value(row.get(sd.field), sd.kind, sd.unit)
    doc_parts = [_text(row.get(f)) for f in cfg.spec_doc_fields]
    spec_doc = " | ".join(p for p in doc_parts if p)
    price, orig, sale = resolve_price(row.get("giá gốc"), row.get("giá khuyến mãi"))
    return Product(
        category=cfg.display, category_code=cfg.code,
        model_code=str(row.get("model_code")), sku=str(row.get("sku")),
        brand=brand, display_name=_build_name(cfg.name_template, row, brand),
        price=price, original_price=orig, sale_price=sale,
        specs=specs, spec_doc=spec_doc,
        promo_text=_text(row.get("khuyến mãi quà")),
        raw={k: (None if _is_nan(v) else v) for k, v in row.items()},
    )


def build_catalog(xlsx_path: str) -> list[Product]:
    products: list[Product] = []
    for cfg in CATEGORY_CONFIGS.values():
        df = pd.read_excel(xlsx_path, sheet_name=cfg.sheet_name)
        for rec in df.to_dict(orient="records"):
            products.append(normalize_row(rec, cfg))
    return products
