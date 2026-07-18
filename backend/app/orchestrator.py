from __future__ import annotations
import re
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
from app.advice.provenance import build_fact_card, format_vnd
from app.advice.budget import budget_alternatives, describe_tradeoff, minimum_budget_options

_BUDGET_INTENTS = (
    ("minimum", re.compile(
        r"\b(?:(?:ngan sach|gia|muc gia).{0,20}(?:toi thieu|thap nhat|re nhat|khoi diem)|"
        r"(?:toi thieu|it nhat|can|phai them|noi ngan sach).{0,20}bao nhieu)\b"
    )),
    ("down", re.compile(
        r"\b(?:(?:gia )?(?:re|thap|mem) hon|bot tien|(?:giam|ha) ngan sach|dat qua|mac qua)\b"
    )),
    ("up", re.compile(
        r"\b(?:cao cap hon|dat hon|(?:nang|tang) ngan sach|chi them tien|loai xin hon)\b"
    )),
)


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
    if not advice.cards:
        return advice.message
    lines = ["Dạ em gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(advice.cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        title = c.title.replace("Vì sao em đề xuất ", "").rstrip("?")
        lines.append(f"{i}. {title} — giá {price}.")
    return "\n".join(lines)


def _budget_intent(message: str) -> str | None:
    flat = " ".join(strip_accents(message.lower()).split())
    return next((intent for intent, pattern in _BUDGET_INTENTS if pattern.search(flat)), None)


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

        if state.stage == "recommended":
            intent = _budget_intent(message)
            if intent == "minimum" and state.profile.category:
                return self._minimum_budget_turn(state)
            if intent in {"down", "up"} and state.last_top_price:
                return self._budget_turn(state, intent)

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
                advice = generate_advice(reco, state.profile, self.llm)
                # Empty-result copy is deterministic. With no cards, numeric verification
                # would reject the user's own budget and produce an empty list heading.
                if advice.cards:
                    advice = verify_advice(advice)
                streamed = False
            state.stage = "recommended"
            state.last_top_price = (reco.top3[0].product.price.value
                                    if reco.top3 and reco.top3[0].product.price.available else None)
            reply = advice.message if not advice.cards or is_grounded(advice) else _safe_summary(advice)
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

    def _minimum_budget_turn(self, state: ChatState):
        options = minimum_budget_options(state.profile, self.store)
        if not options:
            reply = ("Dạ em chưa tính được mức ngân sách tối thiểu vì catalog hiện không có "
                     "sản phẩm vừa giữ các ràng buộc trên vừa có dữ liệu giá ạ.")
            advice = AdviceResult(message=reply, cards=[], assumptions=[], warnings=[])
            return state, TurnResult(
                reply=reply, stage="recommended", advice=advice, need=state.profile
            )

        best = options[0]
        price = int(best.product.price.value)
        reply = ("Dạ, nếu giữ các tiêu chí đã nêu và bỏ giới hạn ngân sách cũ, "
                 f"mức thấp nhất trong catalog là {format_vnd(price)} với "
                 f"{best.product.display_name} ạ.")
        card = build_fact_card(best, state.profile)
        advice = AdviceResult(message=reply, cards=[card], assumptions=[], warnings=[])
        state.profile.budget_min = None
        state.profile.budget_max = price
        state.last_top_price = price
        return state, TurnResult(
            reply=reply, stage="recommended", advice=advice, need=state.profile
        )
