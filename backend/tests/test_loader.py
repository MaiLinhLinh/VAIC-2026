from app.catalog.loader import load_catalog, ProductStore
from app.config import get_settings


def test_store_loads_all_six_categories():
    products = load_catalog(get_settings().catalog_path)
    store = ProductStore(products)
    codes = {p.category_code for p in store.all()}
    assert codes == {"tu_lanh", "may_say", "may_rua_chen", "tu_mat", "dong_ho", "man_hinh"}
    assert len(store.by_category("tu_lanh")) > 1000
