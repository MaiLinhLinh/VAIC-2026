from fastapi.testclient import TestClient
from app.main import app, get_orchestrator
from app.orchestrator import Orchestrator
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue


def _store():
    def mk(brand, price):
        return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                       brand=brand, display_name=f"Tủ lạnh {brand}",
                       price=SourcedValue.of(price, "catalog"),
                       original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                       specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất"),
                              "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất")},
                       spec_doc="", promo_text=None, raw={})
    return ProductStore([mk("A", 12_000_000), mk("B", 11_000_000)])


def _fake_orch():
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "constraints": {"số người": [3, 4]}, "prefs": ["tiết kiệm điện"],
                                   "known": ["category", "budget_max", "constraints", "prefs"]}],
                  text_responses=["Em gợi ý máy giá 12.000.000đ và 11.000.000đ."])
    return Orchestrator(_store(), llm)


def test_health():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_chat_recommends():
    app.dependency_overrides[get_orchestrator] = _fake_orch
    client = TestClient(app)
    r = client.post("/api/chat", json={"session_id": "s1", "message": "nha 4 nguoi mua tu lanh 20tr tiet kiem dien"})
    body = r.json()
    assert r.status_code == 200
    assert body["stage"] == "recommended"
    assert body["recommendation"]["cards"]
    app.dependency_overrides.clear()
