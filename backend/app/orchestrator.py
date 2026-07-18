from __future__ import annotations
from typing import Callable, Optional
from pydantic import BaseModel, Field
from app.schemas import NeedProfile, AdviceResult
from app.llm.client import LLMClient
from app.catalog.loader import ProductStore
from app.nlu.parser import parse_need
from app.nlu.preprocess import strip_accents
from app.dialogue.clarify import next_question, should_recommend, assumptions_for
from app.retrieval.engine import RetrievalEngine
from app.advice.generate import generate_advice
from app.advice.streaming import stream_advice
from app.advice.verify import verify_advice, is_grounded
from app.advice.provenance import build_fact_card
from app.advice.budget import budget_alternatives, describe_tradeoff

_CHEAPER_KW = ["re hon", "gia re hon", "gia thap hon", "bot tien", "giam ngan sach",
               "ha ngan sach", "dat qua", "mac qua", "re hon nua", "gia mem hon"]
_PRICIER_KW = ["cao cap hon", "dat hon nua", "nang ngan sach", "tang ngan sach",
               "chi them tien", "loai xin hon"]


class ChatState(BaseModel):
    profile: NeedProfile = Field(default_factory=NeedProfile)
    asked: list[str] = Field(default_factory=list)
    stage: str = "collecting"
    last_top_price: int | None = None


class TurnResult(BaseModel):
    reply: str
    stage: str
    question: str | None = None
    advice: AdviceResult | None = None
    need: NeedProfile


def _safe_summary(advice: AdviceResult) -> str:
    lines = ["Dạ em gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(advice.cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        title = c.title.replace("Vì sao em đề xuất ", "").rstrip("?")
        lines.append(f"{i}. {title} — giá {price}.")
    return "\n".join(lines)


def _budget_direction(message: str) -> str | None:
    flat = strip_accents(message.lower())
    if any(kw in flat for kw in _CHEAPER_KW):
        return "down"
    if any(kw in flat for kw in _PRICIER_KW):
        return "up"
    return None


class Orchestrator:
    def __init__(self, store: ProductStore, llm: LLMClient):
        self.store = store
        self.llm = llm
        self.engine = RetrievalEngine(store)

    def handle_turn(self, state: ChatState, message: str,
                    on_status: Optional[Callable[[str], None]] = None,
                    on_delta: Optional[Callable[[str], None]] = None):
        # on_status: optional hook so streaming endpoints can surface pipeline progress.
        # on_delta: optional hook — when set, verified advice lines are emitted live
        # as the LLM generates them (see app/advice/streaming.py).
        def notify(text: str) -> None:
            if on_status:
                on_status(text)

        # Budget-adjust intent: only meaningful after a recommendation with a known anchor price.
        if state.stage == "recommended" and state.last_top_price:
            direction = _budget_direction(message)
            if direction is not None:
                return self._budget_turn(state, direction)

        notify("Em đang đọc yêu cầu của anh/chị…")
        state.profile = parse_need(message, self.llm, prior=state.profile)

        if state.profile.category is None:
            return state, TurnResult(
                reply="Dạ anh/chị đang muốn tìm nhóm sản phẩm nào ạ "
                      "(tủ lạnh, máy sấy, máy rửa chén, tủ đông/tủ mát, đồng hồ thông minh, màn hình)?",
                stage="collecting", need=state.profile)

        q = next_question(state.profile, state.asked)
        if q is not None:
            state.asked.append(q.slot)
            return state, TurnResult(reply=q.text, stage="collecting", question=q.text, need=state.profile)

        if should_recommend(state.profile, state.asked):
            for a in assumptions_for(state.profile, state.asked):
                if a not in state.profile.assumptions:
                    state.profile.assumptions.append(a)
            notify("Em đang tìm máy phù hợp trong catalog…")
            reco = self.engine.recommend(state.profile)
            notify("Em đang soạn lời tư vấn…")
            if on_delta is not None:
                advice, streamed = stream_advice(reco, state.profile, self.llm, on_delta)
            else:
                advice, streamed = verify_advice(generate_advice(reco, state.profile, self.llm)), False
            state.stage = "recommended"
            state.last_top_price = (reco.top3[0].product.price.value
                                    if reco.top3 and reco.top3[0].product.price.available else None)
            reply = advice.message if is_grounded(advice) else _safe_summary(advice)
            if advice.assumptions:
                suffix = "\n\n(" + " ".join(advice.assumptions) + ")"
                reply += suffix
                if streamed:
                    on_delta(suffix)  # keep live-emitted text identical to final reply
            return state, TurnResult(reply=reply, stage="recommended", advice=advice, need=state.profile)

        return state, TurnResult(reply="Dạ anh/chị cho em thêm chút thông tin nhé.",
                                 stage="collecting", need=state.profile)

    def _budget_turn(self, state: ChatState, direction: str):
        anchor = state.profile.model_copy(deep=True)
        anchor.budget_max = state.last_top_price
        alts = budget_alternatives(anchor, self.store, direction)
        label = "rẻ hơn" if direction == "down" else "cao cấp hơn"
        if not alts:
            return state, TurnResult(
                reply=f"Dạ em chưa tìm được lựa chọn {label} phù hợp trong catalog ạ.",
                stage="recommended", need=state.profile)
        cards = [build_fact_card(a, state.profile) for a in alts]
        lines = [f"Dạ, vài lựa chọn {label} anh/chị tham khảo:"]
        for a in alts:
            lines.append("- " + describe_tradeoff(a, state.last_top_price))
        # Message is built deterministically from catalog prices (format_vnd) -> grounded by construction.
        advice = AdviceResult(message="\n".join(lines), cards=cards, assumptions=[], warnings=[])
        if alts[0].product.price.available:
            state.last_top_price = alts[0].product.price.value
        return state, TurnResult(reply=advice.message, stage="recommended", advice=advice, need=state.profile)
