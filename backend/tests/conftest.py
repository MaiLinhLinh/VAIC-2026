import os
import pytest
from app.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def ensure_catalog():
    s = get_settings()
    if not os.path.exists(s.catalog_path):
        from app.catalog.normalize import build_catalog
        from app.catalog.loader import save_catalog
        os.makedirs(os.path.dirname(s.catalog_path), exist_ok=True)
        save_catalog(build_catalog(s.dataset_path), s.catalog_path)
