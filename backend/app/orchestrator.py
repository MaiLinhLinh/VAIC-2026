from __future__ import annotations
import re
from typing import Callable, Optional
from pydantic import BaseModel, Field
from app.schemas import NeedProfile, AdviceResult, Product
from app.llm.client import LLMClient
from app.catalog.loader import ProductStore
from app.nlu.parser import parse_need
from app.nlu.preprocess import strip_accents, detect_category
from app.dialogue.clarify import next_question, should_recommend, assumptions_for
from app.dialogue.meta import (
    capability_reply,
    detect_meta_intent,
    greeting_reply,
    options_reply,
    thanks_reply,
)
from app.retrieval.engine import RetrievalEngine
from app.advice.generate import generate_advice
from app.advice.streaming import stream_advice
from app.advice.verify import verify_advice, is_grounded
from app.advice.provenance import build_fact_card, format_vnd
from app.advice.compare import build_comparison
from app.advice.budget import budget_alternatives, describe_tradeoff, minimum_budget_options
from app.advice.detail import resolve_product, is_detail_question, answer_about_product
from app.catalog.capabilities import CALL_CONSTRAINT, call_status, requires_call

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
_LIST_KW = ["may khac", "san pham khac", "cai khac", "lua chon khac", "danh sach",
            "may nao khac", "con gi khac", "quay lai", "xem lai danh sach", "so sanh lai"]


def _wants_product_list(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(k in flat for k in _LIST_KW)


class ChatState(BaseModel):
    profile: NeedProfile = Field(default_factory=NeedProfile)
    asked: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)  # slot khách trả lời "không biết / bỏ qua"
    stage: str = "collecting"
    last_top_price: int | None = None
    last_products: list[Product] = Field(default_factory=list)   # ứng viên của lần đề xuất gần nhất
    focused_sku: str | None = None                              # máy đang được hỏi sâu (multi-turn)


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

        pending_slot = state.asked[-1] if state.stage == "collecting" and state.asked else None
        meta = detect_meta_intent(message, stage=state.stage, pending_slot=pending_slot)
        if meta is not None:
            handled = self._meta_turn(state, meta, pending_slot, notify, on_delta)
            if handled is not None:
                return handled

        if state.stage == "recommended":
            # a) Nâng / hạ / hỏi mức ngân sách tối thiểu
            intent = _budget_intent(message)
            if intent == "minimum" and state.profile.category:
                return self._minimum_budget_turn(state)
            if intent in {"down", "up"} and state.last_top_price:
                return self._budget_turn(state, intent)
            # b) Hỏi kỹ về MỘT sản phẩm cụ thể trong danh sách vừa đề xuất (deep-dive)
            detail = self._detail_turn(state, message)
            if detail is not None:
                return detail

        notify("Em đang đọc yêu cầu của anh/chị…")
        state.profile = parse_need(message, self.llm, prior=state.profile)

        if state.profile.category is None:
            return state, TurnResult(
                reply="Dạ anh/chị đang muốn tìm nhóm sản phẩm nào ạ "
                      "(tủ lạnh, máy sấy, máy rửa chén, tủ đông/tủ mát, đồng hồ thông minh, màn hình)?",
                stage="collecting", need=state.profile)

        q = next_question(state.profile, state.asked, state.skipped)
        if q is not None:
            state.asked.append(q.slot)
            return state, TurnResult(reply=q.text, stage="collecting", question=q.text, need=state.profile)

        if should_recommend(state.profile, state.asked, state.skipped):
            return self._recommend_turn(state, notify, on_delta)

        return state, TurnResult(reply="Dạ anh/chị cho em thêm chút thông tin nhé.",
                                 stage="collecting", need=state.profile)

    def _recommend_turn(self, state: ChatState,
                        notify: Callable[[str], None],
                        on_delta: Optional[Callable[[str], None]]):
        for a in assumptions_for(state.profile, state.asked):
            if a not in state.profile.assumptions:
                state.profile.assumptions.append(a)
        notify("Em đang tìm máy phù hợp trong catalog…")
        reco = self.engine.recommend(state.profile)
        if not reco.top3 and requires_call(state.profile):
            return self._capability_fallback_turn(state)
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
        state.last_products = [s.product for s in reco.top3]
        state.focused_sku = None
        state.last_top_price = (reco.top3[0].product.price.value
                                if reco.top3 and reco.top3[0].product.price.available else None)
        reply = advice.message if not advice.cards or is_grounded(advice) else _safe_summary(advice)
        if advice.assumptions:
            suffix = "\n\n(" + " ".join(advice.assumptions) + ")"
            reply += suffix
            if streamed:
                on_delta(suffix)  # keep live-emitted text identical to final reply
        return state, TurnResult(reply=reply, stage="recommended", advice=advice, need=state.profile)

    def _capability_fallback_turn(self, state: ChatState):
        """Route to truthful nearby choices when a mandatory capability has no exact match."""
        exact_outside_budget = minimum_budget_options(state.profile, self.store)
        if exact_outside_budget:
            lines = [
                "Dạ, trong mức ngân sách đã nêu em chưa tìm được đồng hồ có dữ liệu xác nhận nghe gọi.",
                "Các mẫu gần nhất có xác nhận chức năng này là:",
            ]
            for sp in exact_outside_budget:
                product = sp.product
                price = format_vnd(int(product.price.value))
                extra = ""
                if state.profile.budget_max is not None and product.price.value > state.profile.budget_max:
                    extra = f", cao hơn ngân sách {format_vnd(int(product.price.value - state.profile.budget_max))}"
                lines.append(
                    f"- {product.display_name}: {price}{extra}; khả năng gọi: {call_status(product)}."
                )
            options = exact_outside_budget
        else:
            related_profile = state.profile.model_copy(deep=True)
            related_profile.constraints.pop(CALL_CONSTRAINT, None)
            related = RetrievalEngine(self.store).recommend(related_profile).top3
            if not related:
                related = minimum_budget_options(related_profile, self.store)
            lines = [
                "Dạ, catalog hiện chưa có đồng hồ nào được xác nhận đáp ứng chức năng nghe gọi.",
                "Nếu anh/chị chấp nhận bỏ yêu cầu này, em có thể điều hướng sang các mẫu liên quan sau:",
            ]
            for sp in related:
                product = sp.product
                price = format_vnd(int(product.price.value)) if product.price.available else "chưa có dữ liệu giá"
                status = call_status(product) or "chưa có dữ liệu"
                lines.append(
                    f"- {product.display_name}: {price}; khả năng gọi: {status}."
                )
            if related:
                lines.append("Các mẫu này chỉ để tham khảo và không được coi là đáp ứng yêu cầu nghe gọi ạ.")
            else:
                lines.append("Hiện catalog cũng chưa có sản phẩm liên quan có dữ liệu giá để tham khảo ạ.")
            options = related

        cards = [build_fact_card(sp, state.profile) for sp in options]
        advice = AdviceResult(
            message="\n".join(lines), cards=cards, assumptions=[], warnings=[],
            comparison=build_comparison(options, state.profile),
        )
        state.stage = "recommended"
        state.last_products = [sp.product for sp in options]
        state.focused_sku = None
        state.last_top_price = (
            int(options[0].product.price.value)
            if options and options[0].product.price.available else None
        )
        return state, TurnResult(
            reply=advice.message, stage="recommended", advice=advice, need=state.profile
        )

    def _meta_turn(self, state: ChatState, meta: str, pending_slot: str | None,
                   notify: Callable[[str], None],
                   on_delta: Optional[Callable[[str], None]]):
        """Trả lời các tin nhắn 'meta' (chào hỏi, cảm ơn, hỏi năng lực, hỏi lựa chọn,
        'không biết'). Trả None nếu không xử lý -> đi tiếp pipeline thường."""
        if meta == "greeting":
            return state, TurnResult(reply=greeting_reply(), stage=state.stage, need=state.profile)
        if meta == "thanks":
            return state, TurnResult(reply=thanks_reply(), stage=state.stage, need=state.profile)
        if meta == "capability":
            return state, TurnResult(reply=capability_reply(), stage=state.stage, need=state.profile)
        if meta == "ask_options" and pending_slot is not None:
            return state, TurnResult(
                reply=options_reply(state.profile, pending_slot, self.store),
                stage="collecting", need=state.profile)
        if meta == "dont_know" and pending_slot is not None:
            return self._skip_slot_turn(state, pending_slot, notify, on_delta)
        return None

    def _skip_slot_turn(self, state: ChatState, slot: str,
                        notify: Callable[[str], None],
                        on_delta: Optional[Callable[[str], None]]):
        """Khách không trả lời được câu hỏi đang chờ -> bỏ qua slot đó thay vì hỏi lặp lại."""
        if slot not in state.skipped:
            state.skipped.append(slot)
        note = f"Em tạm bỏ qua '{slot}' vì anh/chị chưa chắc; khi nào rõ hơn em lọc lại giúp ạ."
        if note not in state.profile.assumptions:
            state.profile.assumptions.append(note)
        q = next_question(state.profile, state.asked, state.skipped)
        if q is not None:
            state.asked.append(q.slot)
            text = "Dạ không sao ạ. " + q.text
            return state, TurnResult(reply=text, stage="collecting", question=q.text, need=state.profile)
        if should_recommend(state.profile, state.asked, state.skipped):
            return self._recommend_turn(state, notify, on_delta)
        return state, TurnResult(reply="Dạ không sao ạ. Anh/chị cho em thêm chút thông tin khác nhé.",
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
        advice = AdviceResult(message="\n".join(lines), cards=cards, assumptions=[], warnings=[],
                              comparison=build_comparison(alts, state.profile))
        state.last_products = [a.product for a in alts]
        state.focused_sku = None
        if alts[0].product.price.available:
            state.last_top_price = alts[0].product.price.value
        return state, TurnResult(reply=advice.message, stage="recommended", advice=advice, need=state.profile)

    def _answer_focus(self, state: ChatState, product: Product, message: str):
        state.focused_sku = product.sku
        advice = answer_about_product(product, message, self.llm)
        return state, TurnResult(reply=advice.message, stage="recommended",
                                 advice=advice, need=state.profile)

    def _detail_turn(self, state: ChatState, message: str):
        """Hỏi sâu 1 sản phẩm. 'Sticky focus': đã focus máy nào thì hỏi tiếp áp vào máy đó.
        Trả None nếu không phải luồng hỏi-chi-tiết -> để luồng thường xử lý (tìm kiếm/đề xuất)."""
        # Khách chuyển sang ngành hàng khác -> tìm kiếm mới
        new_cat = detect_category(message)
        if new_cat is not None and new_cat != state.profile.category:
            return None

        # Nhắc tới một máy cụ thể (vị trí/hãng/rẻ-đắt nhất) -> focus máy đó
        p = resolve_product(message, state.last_products)
        if p is not None:
            return self._answer_focus(state, p, message)

        # Đang focus 1 máy & không đòi xem lại danh sách -> hỏi tiếp về máy đó
        if state.focused_sku and not _wants_product_list(message):
            focused = next((x for x in state.last_products if x.sku == state.focused_sku), None)
            if focused is not None:
                return self._answer_focus(state, focused, message)

        # Chưa focus máy nào nhưng có ý hỏi chi tiết -> hỏi lại cho rõ
        if is_detail_question(message) and state.last_products:
            names = " · ".join(f"{i+1}. {x.display_name}"
                               for i, x in enumerate(state.last_products))
            return state, TurnResult(
                reply=f"Dạ anh/chị muốn tìm hiểu kỹ máy nào ạ? ({names})",
                stage="recommended", need=state.profile)
        return None

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
