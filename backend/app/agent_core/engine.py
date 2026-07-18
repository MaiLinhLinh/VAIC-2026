from __future__ import annotations
from typing import Any, Callable, Dict, Optional, Protocol
from app.config import get_settings
from app.llm.client import get_llm
from app.agent_core.agent_engine import get_compiled_graph


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


class AgentCoreEngine:
    """Phục vụ 1 lượt qua LangGraph. Memory qua MemorySaver (thread_id có version cho reset).
    Runtime deps (llm, db_path, callbacks) truyền qua config['configurable'], không checkpoint."""

    def __init__(self, llm: Any = None, db_path: Optional[str] = None):
        self.llm = llm if llm is not None else get_llm()
        self.db_path = db_path or get_settings().agent_db_path
        self.graph = get_compiled_graph()
        self._epoch: Dict[str, int] = {}

    def _thread(self, sid: str) -> str:
        return f"{sid}:{self._epoch.get(sid, 0)}"

    def handle(self, session_id: str, message: str,
               on_status: Optional[Callable[[str], None]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
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
        return {"reply": result.get("response", ""), "stage": stage,
                "question": result.get("question"), "need": _need_from_intent(intent),
                "recommendation": recommendation}

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
