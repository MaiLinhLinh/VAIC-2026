from app.config import Settings
from app.agent_core.retriever import search_products, get_catalog_metadata
from tests.agent_helpers import make_db


def test_settings_defaults():
    s = Settings()
    assert s.pipeline in ("agent_core", "orchestrator")
    assert s.agent_db_path.endswith("products.db")


def test_search_uses_explicit_db(tmp_path):
    db = str(tmp_path / "t.db")
    make_db(db, [{"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL1", "price_clean": 12_000_000,
                  "specs": {"Dung tích tổng": "300 lít"}}])
    res = search_products("tủ lạnh", category="Tủ Lạnh", db_path=db)
    assert res["status"] == "exact_match"
    assert res["total_matches_found"] == 1
    assert res["top_3_products"][0]["brand"] == "Toshiba"


def test_metadata_lists_categories(tmp_path):
    db = str(tmp_path / "t.db")
    make_db(db, [{"category": "Máy giặt", "brand": "LG", "price_clean": 9_000_000, "specs": {}}])
    meta = get_catalog_metadata(db)
    assert "Máy giặt" in meta["categories"]
