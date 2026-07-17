from __future__ import annotations
from app.orchestrator import ChatState


class SessionStore:
    def __init__(self):
        self._states: dict[str, ChatState] = {}

    def get(self, sid: str) -> ChatState:
        return self._states.setdefault(sid, ChatState())

    def set(self, sid: str, state: ChatState) -> None:
        self._states[sid] = state

    def reset(self, sid: str) -> None:
        self._states.pop(sid, None)


SESSIONS = SessionStore()


def mask(text: str) -> str:
    # không log nội dung khách; chỉ log độ dài (bảo mật PII)
    return f"<{len(text)} chars>"
