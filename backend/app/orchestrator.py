from __future__ import annotations
from pydantic import BaseModel, Field
from app.schemas import NeedProfile, AdviceResult, Product
from app.llm.client import LLMClient
from app.catalog.loader import ProductStore
from app.nlu.parser import parse_need
from app.nlu.preprocess import strip_accents, detect_category
from app.dialogue.clarify import next_question, should_recommend, assumptions_for
from app.retrieval.engine import RetrievalEngine
from app.advice.generate import generate_advice
from app.advice.verify import verify_advice, is_grounded
from app.advice.provenance import build_fact_card
from app.advice.compare import build_comparison
from app.advice.budget import budget_alternatives, describe_tradeoff
from app.advice.detail import resolve_product, is_detail_question, answer_about_product

_CHEAPER_KW = ["re hon", "gia re hon", "gia thap hon", "bot tien", "giam ngan sach",
               "ha ngan sach", "dat qua", "mac qua", "re hon nua", "gia mem hon"]
_PRICIER_KW = ["cao cap hon", "dat hon nua", "nang ngan sach", "tang ngan sach",
               "chi them tien", "loai xin hon"]
_LIST_KW = ["may khac", "san pham khac", "cai khac", "lua chon khac", "danh sach",
            "may nao khac", "con gi khac", "quay lai", "xem lai danh sach", "so sanh lai"]


def _wants_product_list(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(k in flat for k in _LIST_KW)


class ChatState(BaseModel):
    profile: NeedProfile = Field(default_factory=NeedProfile)
    asked: list[str] = Field(default_factory=list)
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

    def handle_turn(self, state: ChatState, message: str):
        if state.stage == "recommended":
            # a) Nâng/hạ ngân sách
            if state.last_top_price:
                direction = _budget_direction(message)
                if direction is not None:
                    return self._budget_turn(state, direction)
            # b) Hỏi kỹ về MỘT sản phẩm cụ thể trong danh sách vừa đề xuất
            detail = self._detail_turn(state, message)
            if detail is not None:
                return detail

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
            reco = self.engine.recommend(state.profile)
            advice = verify_advice(generate_advice(reco, state.profile, self.llm))
            state.stage = "recommended"
            state.last_products = [s.product for s in reco.top3]
            state.focused_sku = None
            state.last_top_price = (reco.top3[0].product.price.value
                                    if reco.top3 and reco.top3[0].product.price.available else None)
            reply = advice.message if is_grounded(advice) else _safe_summary(advice)
            if advice.assumptions:
                reply += "\n\n(" + " ".join(advice.assumptions) + ")"
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
