from __future__ import annotations
import json
import time
from queue import Queue
from threading import Thread
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.orchestrator import Orchestrator, TurnResult
from app.catalog.loader import get_store
from app.llm.client import get_llm
from app.session import SESSIONS

app = FastAPI(title="Trợ lý AI Điện Máy Xanh")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])

# Typewriter fallback for turns with no live LLM stream (clarify questions, stream
# errors): the finished reply is sent in slices of this many characters with a small
# delay. Recommendation turns instead stream verified lines live via on_delta.
STREAM_CHUNK_CHARS = 12
STREAM_CHUNK_DELAY_S = 0.02
# Live verified lines are re-sliced into these smaller pieces for a smooth
# ChatGPT-style typing effect; while a line is being "typed out", the worker
# thread keeps reading the LLM stream so gaps between lines are filled.
LIVE_SLICE_CHARS = 4
LIVE_SLICE_DELAY_S = 0.02


class ChatIn(BaseModel):
    session_id: str
    message: str


class ResetIn(BaseModel):
    session_id: str


def get_orchestrator() -> Orchestrator:
    return Orchestrator(get_store(), get_llm())


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
def chat(body: ChatIn, orch: Orchestrator = Depends(get_orchestrator)):
    state = SESSIONS.get(body.session_id)
    state, result = orch.handle_turn(state, body.message)
    SESSIONS.set(body.session_id, state)
    return _turn_payload(result)


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, orch: Orchestrator = Depends(get_orchestrator)):
    """SSE variant of /api/chat. Events (one JSON object per `data:` line):
    {type:"status", text}  — pipeline progress, sent while the turn is processed
    {type:"delta", text}   — verified reply text: advice lines arrive LIVE as the
                             LLM writes them (each line grounding-checked before
                             emission); other turns get a typewriter of the reply
    {type:"done", ...}     — same payload as /api/chat, marks end of stream; the
                             client replaces the streamed bubble with done.reply
                             (retraction path if line verification aborted the stream)
    {type:"error"}         — processing failed
    """
    def event_gen():
        q: Queue = Queue()

        def run_turn():
            # The turn runs in a worker thread so status/delta events can be flushed
            # to the client while the LLM calls are still in flight.
            try:
                state = SESSIONS.get(body.session_id)
                state, result = orch.handle_turn(
                    state, body.message,
                    on_status=lambda t: q.put(("status", t)),
                    on_delta=lambda t: q.put(("delta", t)))
                SESSIONS.set(body.session_id, state)
                q.put(("result", result))
            except Exception:
                q.put(("error", None))

        Thread(target=run_turn, daemon=True).start()
        live = False  # True once any live delta was forwarded
        while True:
            kind, val = q.get()
            if kind == "status":
                yield _sse({"type": "status", "text": val})
            elif kind == "delta":
                # val is one VERIFIED line — type it out in small slices for smoothness.
                live = True
                for i in range(0, len(val), LIVE_SLICE_CHARS):
                    yield _sse({"type": "delta", "text": val[i:i + LIVE_SLICE_CHARS]})
                    time.sleep(LIVE_SLICE_DELAY_S)
            elif kind == "error":
                yield _sse({"type": "error"})
                return
            else:
                if not live:
                    # No live stream this turn (clarify question, no-match, stream
                    # fallback) — deliver the finished reply typewriter-style.
                    reply = val.reply
                    for i in range(0, len(reply), STREAM_CHUNK_CHARS):
                        yield _sse({"type": "delta", "text": reply[i:i + STREAM_CHUNK_CHARS]})
                        time.sleep(STREAM_CHUNK_DELAY_S)
                # If live deltas were sent they either equal done.reply (client's
                # replacement is a no-op) or verification aborted mid-stream and
                # done.reply (safe summary) replaces the partial text.
                yield _sse({"type": "done", **_turn_payload(val)})
                return

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/reset")
def reset(body: ResetIn):
    SESSIONS.reset(body.session_id)
    return {"status": "reset"}
