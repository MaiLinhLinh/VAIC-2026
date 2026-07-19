import os
import tempfile
from fastapi.testclient import TestClient
from app.main import app, get_engine
from app.agent_core.engine import AgentCoreEngine
from app.llm.client import FakeLLM
from tests.agent_helpers import make_db


def _engine_factory():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "api.db")
    make_db(db, [
        {"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL1", "price_clean": 12_400_000,
         "specs": {"Dung tích tổng": "300 lít"}},
        {"category": "Tủ Lạnh", "brand": "LG", "model_code": "TL2", "price_clean": 11_000_000,
         "specs": {"Dung tích tổng": "250 lít"}},
    ])
    llm = FakeLLM(
        json_responses=[{"category": "Tủ Lạnh", "budget_max": 20000000, "priority_features": [],
                         "needs_clarification": False, "is_meta_inquiry": False,
                         "clarification_questions": [], "brand": None}] * 5,
        text_responses=["Máy Toshiba giá 12.400.000đ, LG giá 11.000.000đ."] * 5)
    return AgentCoreEngine(llm=llm, db_path=db)


def test_chat_agent_core_shape():
    app.dependency_overrides[get_engine] = _engine_factory
    try:
        client = TestClient(app)
        r = client.post("/api/chat", json={"session_id": "a1", "message": "mua tủ lạnh 20tr"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"reply", "stage", "question", "need", "recommendation", "trace"}
        assert [item["step"] for item in body["trace"]] == ["intent", "dialogue", "retrieval", "timing"]
        retrieval_trace = body["trace"][2]["data"]
        assert retrieval_trace["status"] in {"exact_match", "scored_match"}
        assert retrieval_trace["total_matches_found"] == 2
        assert retrieval_trace["description_search"]["column"] == "search_description"
        assert isinstance(retrieval_trace["candidate_params"], list)
        assert body["recommendation"]["comparison"] is not None
    finally:
        app.dependency_overrides.clear()


def test_reset_endpoint():
    app.dependency_overrides[get_engine] = _engine_factory
    try:
        client = TestClient(app)
        client.post("/api/chat", json={"session_id": "a2", "message": "mua tủ lạnh 20tr"})
        r = client.post("/api/reset", json={"session_id": "a2"})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_stream_yields_done():
    app.dependency_overrides[get_engine] = _engine_factory
    try:
        client = TestClient(app)
        r = client.post("/api/chat/stream", json={"session_id": "a3", "message": "mua tủ lạnh 20tr"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert '"type": "done"' in r.text
    finally:
        app.dependency_overrides.clear()
