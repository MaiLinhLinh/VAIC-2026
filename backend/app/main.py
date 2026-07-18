from __future__ import annotations
import json
import time
from queue import Queue
from threading import Thread
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.orchestrator import TurnResult
from app.catalog.loader import get_store
from app.llm.client import get_llm
from app.config import get_settings
from app.agent_core.engine import AgentCoreEngine, OrchestratorEngine, Engine

app = FastAPI(title="Trợ lý AI Điện Máy Xanh")
_origins = [o.strip() for o in get_settings().frontend_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["*"], allow_headers=["*"])

# Typewriter fallback for turns with no live LLM stream (clarify questions, stream
# errors): the finished reply is sent in slices of this many characters with a small delay.
STREAM_CHUNK_CHARS = 12
STREAM_CHUNK_DELAY_S = 0.02
# Live verified lines are re-sliced into these smaller pieces for a smooth typing effect.
LIVE_SLICE_CHARS = 4
LIVE_SLICE_DELAY_S = 0.02

_AGENT_ENGINE: AgentCoreEngine | None = None


class ChatIn(BaseModel):
    session_id: str
    message: str


class ResetIn(BaseModel):
    session_id: str


def get_engine() -> Engine:
    """Chọn engine theo cờ PIPELINE. agent_core dùng singleton để giữ MemorySaver + epoch."""
    global _AGENT_ENGINE
    if get_settings().pipeline == "orchestrator":
        return OrchestratorEngine(get_store(), get_llm())
    if _AGENT_ENGINE is None:
        _AGENT_ENGINE = AgentCoreEngine()
    return _AGENT_ENGINE


def _turn_payload(result: TurnResult) -> dict:
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


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@app.get("/api/health")
def health():
    try:
        n = len(get_store().all())
    except Exception:
        n = 0
    return {"status": "ok", "products": n}


@app.post("/api/chat")
def chat(body: ChatIn, engine: Engine = Depends(get_engine)):
    return engine.handle(body.session_id, body.message)


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, engine: Engine = Depends(get_engine)):
    """SSE variant of /api/chat. Events: status (progress), delta (verified reply slices),
    done (full payload), error. Contract unchanged from the orchestrator version."""
    def event_gen():
        q: Queue = Queue()

        def run_turn():
            # Turn runs in a worker thread so status/delta events flush while LLM calls are in flight.
            try:
                payload = engine.handle(
                    body.session_id, body.message,
                    on_status=lambda t: q.put(("status", t)),
                    on_delta=lambda t: q.put(("delta", t)))
                q.put(("result", payload))
            except Exception:
                q.put(("error", None))

        Thread(target=run_turn, daemon=True).start()
        live = False
        while True:
            kind, val = q.get()
            if kind == "status":
                yield _sse({"type": "status", "text": val})
            elif kind == "delta":
                live = True
                for i in range(0, len(val), LIVE_SLICE_CHARS):
                    yield _sse({"type": "delta", "text": val[i:i + LIVE_SLICE_CHARS]})
                    time.sleep(LIVE_SLICE_DELAY_S)
            elif kind == "error":
                yield _sse({"type": "error"})
                return
            else:
                if not live:
                    # No live stream this turn (clarify, no-match, stream fallback) -> typewriter.
                    reply = val["reply"]
                    for i in range(0, len(reply), STREAM_CHUNK_CHARS):
                        yield _sse({"type": "delta", "text": reply[i:i + STREAM_CHUNK_CHARS]})
                        time.sleep(STREAM_CHUNK_DELAY_S)
                yield _sse({"type": "done", **val})
                return

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
def reset(body: ResetIn, engine: Engine = Depends(get_engine)):
    engine.reset(body.session_id)
    return {"status": "reset"}
