from __future__ import annotations
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app.orchestrator import Orchestrator
from app.catalog.loader import get_store
from app.llm.client import get_llm
from app.session import SESSIONS

app = FastAPI(title="Trợ lý AI Điện Máy Xanh")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


class ChatIn(BaseModel):
    session_id: str
    message: str


class ResetIn(BaseModel):
    session_id: str


def get_orchestrator() -> Orchestrator:
    return Orchestrator(get_store(), get_llm())


@app.get("/api/health")
def health():
    try:
        n = len(get_store().all())
    except Exception:
        n = 0
    return {"status": "ok", "products": n}


@app.post("/api/chat")
def chat(body: ChatIn, orch: Orchestrator = Depends(get_orchestrator)):
    state = SESSIONS.get(body.session_id)
    state, result = orch.handle_turn(state, body.message)
    SESSIONS.set(body.session_id, state)
    recommendation = None
    if result.advice is not None:
        recommendation = {
            "cards": [c.model_dump() for c in result.advice.cards],
            "assumptions": result.advice.assumptions,
            "warnings": result.advice.warnings,
            "comparison": (result.advice.comparison.model_dump()
                           if result.advice.comparison else None),
        }
    return {"reply": result.reply, "stage": result.stage,
            "question": result.question, "need": result.need.model_dump(),
            "recommendation": recommendation}


@app.post("/api/reset")
def reset(body: ResetIn):
    SESSIONS.reset(body.session_id)
    return {"status": "reset"}
