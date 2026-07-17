import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.catalog.normalize import build_catalog
from app.catalog.loader import save_catalog
from app.config import get_settings


def main():
    s = get_settings()
    products = build_catalog(s.dataset_path)
    os.makedirs(os.path.dirname(s.catalog_path), exist_ok=True)
    save_catalog(products, s.catalog_path)
    print(f"Normalized {len(products)} products -> {s.catalog_path}")


if __name__ == "__main__":
    main()
