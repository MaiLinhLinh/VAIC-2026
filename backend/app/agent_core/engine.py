from __future__ import annotations
import sqlite3
import time
from typing import Any, Callable, Dict, Optional, Protocol
from app.config import get_settings
from app.llm.client import get_llm
from app.agent_core.agent_engine import get_compiled_graph
from app.agent_core.search_description import ensure_search_descriptions


class Engine(Protocol):
    def handle(self, session_id: str, message: str,
               on_status: Optional[Callable[[str], None]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]: ...

    def reset(self, session_id: str) -> None: ...


def _need_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    # Map intent -> shape NeedProfile để giữ field cho contract (frontend không render need).
    return {"category": intent.get("category"), "budget_min": None,
            "budget_max": intent.get("budget_max"), "constraints": {},
            "prefs": intent.get("priority_features", []), "demographics": {},
            "known": [], "assumptions": []}


def _trace_from_result(message: str, result: Dict[str, Any], elapsed_ms: int) -> list[Dict[str, Any]]:
    """Compact, JSON-safe pipeline trace for the developer panel in the frontend."""
    intent = result.get("intent", {}) or {}
    slots = result.get("slots", []) or []
    retrieval = result.get("retrieval", {}) or {}
    trace: list[Dict[str, Any]] = [
        {"step": "intent", "title": "Phân tích yêu cầu", "data": {
            "query": message,
            "category": intent.get("category"),
            "budget_min": intent.get("budget_min"),
            "budget_max": intent.get("budget_max"),
            "brand": intent.get("brand"),
            "priority_features": intent.get("priority_features") or [],
            "required_features": intent.get("required_features") or [],
            "wants_comparison": bool(intent.get("wants_comparison")),
            "declines_more_info": bool(intent.get("declines_more_info")),
            "is_meta_inquiry": bool(intent.get("is_meta_inquiry")),
        }},
        {"step": "dialogue", "title": "Trạng thái hội thoại", "data": {
            "stage": result.get("stage", "collecting"),
            "next_question": result.get("question"),
            "clarify_count": result.get("clarify_count", 0),
            "slot_stage": result.get("slot_stage"),
            "comparison_followup": bool(result.get("comparison_followup")),
            "slots": [
                {key: slot.get(key) for key in ("name", "value", "status", "basis", "hard")}
                for slot in slots
            ],
        }},
    ]
    if retrieval:
        trace.append({"step": "retrieval", "title": "Truy xuất catalog", "data": {
            "status": retrieval.get("status"),
            "candidate_sql": retrieval.get("sql_display") or retrieval.get("sql_query"),
            "candidate_params": retrieval.get("sql_params") or [],
            "description_search": retrieval.get("description_search") or {},
            "description_evidence": retrieval.get("description_evidence") or [],
            "relaxed_features": retrieval.get("relaxed_features") or [],
            "hard_filters": retrieval.get("hard_filters") or {},
            "total_matches_found": retrieval.get("total_matches_found", 0),
            "returned_products": [
                str(row.get("name") or row.get("model_code") or row.get("sku") or "")
                for row in retrieval.get("top_3_products", [])
            ],
        }})
    trace.append({"step": "timing", "title": "Thời gian xử lý", "data": {
        "total_ms": elapsed_ms,
    }})
    return trace


class AgentCoreEngine:
    """Phục vụ 1 lượt qua LangGraph. Memory qua MemorySaver (thread_id có version cho reset).
    Runtime deps (llm, db_path, callbacks) truyền qua config['configurable'], không checkpoint."""

    def __init__(self, llm: Any = None, db_path: Optional[str] = None):
        self.llm = llm if llm is not None else get_llm()
        self.db_path = db_path or get_settings().agent_db_path
        # Migration tương thích cho products.db cũ. DB build mới đã có sẵn cột nên chỉ
        # thực hiện một COUNT nhanh; nếu file chỉ đọc, retriever vẫn có fallback an toàn.
        try:
            ensure_search_descriptions(self.db_path)
        except (OSError, RuntimeError, sqlite3.Error):
            pass
        self.graph = get_compiled_graph()
        self._epoch: Dict[str, int] = {}

    def _thread(self, sid: str) -> str:
        return f"{sid}:{self._epoch.get(sid, 0)}"

    def handle(self, session_id: str, message: str,
               on_status: Optional[Callable[[str], None]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        started = time.perf_counter()
        config = {"configurable": {"thread_id": self._thread(session_id),
                                   "llm": self.llm, "db_path": self.db_path,
                                   "on_status": on_status, "on_delta": on_delta}}
        result = self.graph.invoke({"query": message}, config=config)
        intent = result.get("intent", {})
        stage = result.get("stage", "collecting")
        recommendation = None
        if stage == "recommended":
            recommendation = {"cards": result.get("cards", []),
                              "assumptions": result.get("assumptions", []),
                              "warnings": result.get("warnings", []),
                              "comparison": result.get("comparison")}
        trace = _trace_from_result(message, result, round((time.perf_counter() - started) * 1000))
        return {"reply": result.get("response", ""), "stage": stage,
                "question": result.get("question"), "need": _need_from_intent(intent),
                "recommendation": recommendation, "trace": trace}

    def reset(self, session_id: str) -> None:
        self._epoch[session_id] = self._epoch.get(session_id, 0) + 1


class OrchestratorEngine:
    """Adapter bọc Orchestrator cũ về cùng interface (dùng khi PIPELINE=orchestrator / test cũ)."""

    def __init__(self, store, llm):
        from app.orchestrator import Orchestrator
        from app.session import SESSIONS
        self.orch = Orchestrator(store, llm)
        self.sessions = SESSIONS

    def handle(self, session_id: str, message: str, on_status=None, on_delta=None) -> Dict[str, Any]:
        from app.main import _turn_payload
        state = self.sessions.get(session_id)
        state, result = self.orch.handle_turn(state, message, on_status=on_status, on_delta=on_delta)
        self.sessions.set(session_id, state)
        return _turn_payload(result)

    def reset(self, session_id: str) -> None:
        self.sessions.reset(session_id)
