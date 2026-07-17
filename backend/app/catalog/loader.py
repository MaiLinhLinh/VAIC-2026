from __future__ import annotations
import json
from functools import lru_cache
from app.schemas import Product
from app.config import get_settings


def save_catalog(products: list[Product], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in products], f, ensure_ascii=False)


def load_catalog(path: str) -> list[Product]:
    with open(path, encoding="utf-8") as f:
        return [Product(**d) for d in json.load(f)]


class ProductStore:
    def __init__(self, products: list[Product]):
        self._all = products
        self._by_cat: dict[str, list[Product]] = {}
        for p in products:
            self._by_cat.setdefault(p.category_code, []).append(p)

    def all(self) -> list[Product]:
        return self._all

    def by_category(self, code: str) -> list[Product]:
        return self._by_cat.get(code, [])


@lru_cache
def get_store() -> ProductStore:
    return ProductStore(load_catalog(get_settings().catalog_path))
