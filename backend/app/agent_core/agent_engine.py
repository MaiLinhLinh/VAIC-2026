from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agent_core.intent import (extract_intent, has_enough_slots, kw_declines, kw_policy,
                                   is_off_topic_request, is_programming_request)
from app.agent_core.policy import answer_policy
from app.agent_core.retriever import (search_products, price_spread_products, get_catalog_metadata,
                                       category_table_for, hydrate_rows)
from app.agent_core.sql_tool import agent_query
from app.agent_core.advisor import build_cards, generate_advisor
from app.agent_core.compare import build_comparison
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail, closing_hook)
from app.agent_core.sales import (is_order_confirmation, is_aftersales_question,
                                  cross_sell_suggestion, cross_sell_line)
from app.agent_core.presenters import product_display_name, load_specs, build_detail_card
from app.advice.provenance import format_vnd
from app.agent_core.addressing import DEFAULT_ADDRESS, resolve_address, resolve_self_term
from app.advice.verify import verify_advice, is_grounded
from app.schemas import AdviceResult

log = logging.getLogger("agent_core")


class AgentState(TypedDict, total=False):
    """State được MemorySaver checkpoint -> chỉ chứa dữ liệu serialize được.
    Runtime deps (llm, db_path, callbacks) truyền qua config['configurable'], KHÔNG để trong state."""
    query: str
    history: List[Dict[str, str]]
    intent: Dict[str, Any]
    retrieval: Dict[str, Any]
    last_products: List[Dict[str, Any]]
    focused_sku: Optional[str]
    stage: str
    question: Optional[str]
    response: str
    cards: List[Dict[str, Any]]
    comparison: Optional[Dict[str, Any]]
    assumptions: List[str]
    warnings: List[str]
    next_action: str
    clarify_count: int
    customer_addr: str
    purchase_history: List[Dict[str, Any]]


def _cfg(config, key, default=None):
    return (config or {}).get("configurable", {}).get(key, default)


def _addr(state: AgentState) -> str:
    """Cách gọi khách cho lượt hiện tại (suy ra ở intent_node, mặc định 'anh/chị')."""
    return state.get("customer_addr") or DEFAULT_ADDRESS


def _addr_cap(state: AgentState) -> str:
    """Bản viết hoa chữ cái đầu của _addr(), dùng khi đứng đầu câu."""
    addr = _addr(state)
    return addr[0].upper() + addr[1:]


def _self(state: AgentState) -> str:
    """Bot tự xưng gì cho lượt hiện tại, đối ứng với _addr() (VD gọi khách 'ông' -> xưng 'cháu')."""
    return resolve_self_term(_addr(state))


def _notify(config, text: str) -> None:
    cb = _cfg(config, "on_status")
    if cb:
        cb(text)


def _sku(row: Dict[str, Any]) -> str:
    return str(row.get("model_code") or row.get("sku") or product_display_name(row))


def intent_node(state: AgentState, config) -> AgentState:
    query = state.get("query", "")
    history = list(state.get("history", []))
    customer_addr = resolve_address(query, state.get("customer_addr"))
    self_term = resolve_self_term(customer_addr)
    _notify(config, f"{self_term.capitalize()} đang đọc yêu cầu của {customer_addr}…")
    intent = extract_intent(query, history, _cfg(config, "llm"), _cfg(config, "db_path"),
                            addr=customer_addr, self_term=self_term)
    # Lưới dự phòng: keyword bắt được từ chối thì tin, kể cả khi LLM bỏ sót.
    if kw_declines(query):
        intent["declines_more_info"] = True
    # Lưới dự phòng chính sách: chỉ khi không dính nhu cầu sản phẩm nào (tránh cướp
    # câu hỏi bảo hành/trả góp của một sản phẩm cụ thể đang tư vấn).
    if kw_policy(query) and not intent.get("category"):
        intent["is_policy_question"] = True
    log.info("intent_node: query=%r -> category=%r budget_max=%s brand=%r feats=%s "
             "assumptions=%s declines=%s needs_clarification=%s meta=%s",
             query, intent.get("category"), intent.get("budget_max"), intent.get("brand"),
             intent.get("priority_features"), intent.get("assumptions"),
             intent.get("declines_more_info"), intent.get("needs_clarification"),
             intent.get("is_meta_inquiry"))
    history = history + [{"role": "user", "content": query}]
    return {"intent": intent, "history": history, "customer_addr": customer_addr}


def _is_detail_followup(state: AgentState) -> bool:
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    if not last:
        return False
    intent = state.get("intent", {})
    # Đổi ngành hàng -> tìm mới, không phải hỏi chi tiết.
    cat = intent.get("category")
    if cat and last and last[0].get("category") and cat != last[0].get("category"):
        return False
    if resolve_product_row(query, last) is not None:
        return True
    if state.get("focused_sku") and is_detail_question(query) and not wants_product_list(query):
        return True
    return False


# Trần số lượt hỏi lại cho cả phiên: quá trần thì tư vấn luôn thay vì hỏi mãi.
_MAX_CLARIFY = 3


def router_edge(state: AgentState) -> str:
    intent = state.get("intent", {})
    query = state.get("query", "")
    count = state.get("clarify_count", 0)
    # Luật cứng: ngành hàng và NGÂN SÁCH là bắt buộc trước khi đề xuất —
    # 3. Yêu cầu category, không bắt buộc ngân sách nữa
    missing_required = not intent.get("category")
    declines = bool(intent.get("declines_more_info"))
    # Phạm vi catalog phải thắng policy: nếu khách hỏi chính sách cho một mặt hàng
    # không kinh doanh thì không được tra tài liệu rồi trả nhầm chính sách nhóm khác.
    if intent.get("unsupported_product"):
        route = "unsupported"
    # Khách chốt đơn (xác nhận mua) một máy đang bàn -> ghi nhận lịch sử mua hàng
    # ngay, thắng mọi nhánh khác (đây là hành động rõ ràng của khách).
    elif is_order_confirmation(query) and (state.get("last_products") or state.get("focused_sku")):
        route = "confirm_purchase"
    # Khách hỏi về máy/đơn ĐÃ MUA trước đó (chăm sóc sau mua) -> tra theo lịch sử mua
    # hàng của phiên, không lẫn với câu hỏi bảo hành của máy đang xem lần đầu.
    elif is_aftersales_question(query):
        route = "aftersales"
    # Cờ policy vẫn xét trước detail: câu như "phí lắp đặt thế nào" dính cả keyword
    # detail nhưng phí/vận hành chỉ có trong tài liệu chính sách.
    elif intent.get("is_policy_question"):
        route = "policy"
    elif _is_detail_followup(state):
        route = "detail"
    elif intent.get("is_chitchat"):
        # Xã giao/ngoài chủ đề: đáp thân thiện rồi lái về mua sắm, không đụng catalog.
        route = "chitchat"
    elif intent.get("is_meta_inquiry"):
        route = "meta_inquiry"
    elif not intent.get("category"):
        # Luật thép: không bao giờ đề xuất khi chưa rõ ngành hàng (kể cả hết quota hỏi
        # hay khách từ chối) — đề xuất ngẫu nhiên toàn kho tệ hơn một câu hỏi thêm.
        route = "clarify"
    elif declines:
        # Từ chối chỉ miễn câu hỏi ngân sách/nhu cầu; ngành đã rõ -> tư vấn 3 tầm giá.
        route = "retrieve"
    elif count >= _MAX_CLARIFY:
        route = "retrieve"
    elif missing_required or (intent.get("needs_clarification") and not has_enough_slots(intent)):
        route = "clarify"
    else:
        route = "retrieve"
    log.info("router: -> %s (missing_required=%s, declines=%s, needs_clarification=%s, "
             "has_enough_slots=%s, clarify_count=%d, last_products=%d, focused_sku=%r)",
             route, missing_required, declines, intent.get("needs_clarification"),
             has_enough_slots(intent), count, len(state.get("last_products") or []),
             state.get("focused_sku"))
    return route


def clarify_node(state: AgentState, config) -> AgentState:
    intent = state.get("intent", {})
    cat = intent.get("category")
    count = state.get("clarify_count", 0)
    addr = _addr(state)
    self_term = _self(state)
    # Câu hỏi do AI soạn theo bối cảnh khách kể — dùng nguyên văn; luật chỉ vá khi thiếu.
    qs = [q.strip() for q in (intent.get("clarification_questions") or []) if q.strip()][:2]
    if not cat and not qs:
        cats = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
        qs = [f"Bên {self_term} đang có: " + ", ".join(cats) + f". {_addr_cap(state)} đang cần nhóm sản phẩm nào ạ?"]
    # Luật "chốt sổ": đây là câu hỏi cuối của quota mà ngân sách vẫn trống -> phải phủ ngân sách.
    _money = ("ngân sách", "giá", "bao nhiêu", "tiền", "triệu", "tầm")
    if (cat and not intent.get("budget_max") and count >= _MAX_CLARIFY - 1
            and not any(w in q.lower() for q in qs for w in _money)):
        qs = (qs + [f"{_addr_cap(state)} dự tính ngân sách khoảng bao nhiêu ạ?"])[-2:]
    # Chỉ chào ở lượt trợ lý mở lời đầu tiên của phiên; các lượt sau vào thẳng câu hỏi.
    greeted = any(m.get("role") == "assistant" for m in state.get("history", []))
    transition = intent.get("transition_message")
    if transition:
        opener = transition
    elif greeted:
        opener = f"Dạ, {self_term} cần thêm chút thông tin để chọn đúng máy cho mình:"
    elif cat:
        opener = f"Chào {addr}! Để tư vấn chuẩn dòng **{cat}** theo đúng nhu cầu, {addr} chia sẻ thêm giúp {self_term}:"
    else:
        opener = f"Chào {addr}! Để tư vấn đúng nhu cầu, {addr} chia sẻ thêm giúp {self_term}:"
    text = opener + "\n\n" + "\n".join(f"- {q}" for q in qs)
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "question": qs[0] if qs else None, "stage": "collecting",
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history,
            "clarify_count": state.get("clarify_count", 0) + 1}


_DIGITAL_CATEGORIES = ("Máy tính bảng", "Máy tính để bàn", "Màn hình máy tính")


def _chitchat_fallback(addr: str, self_term: str) -> str:
    return (f"Dạ, {self_term} chưa trả lời tốt câu hỏi này ạ. "
            f"Nếu {addr} cần tư vấn sản phẩm, {self_term} hỗ trợ ngay.")


def _digital_suggestions(categories: List[str], limit: int) -> List[str]:
    return [cat for cat in _DIGITAL_CATEGORIES if cat in categories][:limit]


def _off_topic_suggestions(query: str, categories: List[str]) -> List[str]:
    if is_programming_request(query):
        return _digital_suggestions(categories, 3)
    return categories[:3]


def _has_shopping_pivot(reply: str, categories: List[str]) -> bool:
    flat = reply.lower()
    signals = ("tư vấn", "mua sắm", "sản phẩm", "thiết bị", "đang cần tìm", "quan tâm nhóm")
    return any(signal in flat for signal in signals) or any(cat.lower() in flat for cat in categories)


def _knowledge_pivot(categories: List[str], addr: str, self_term: str) -> str:
    suggestions = _digital_suggestions(categories, 2)
    if not suggestions:
        return f"{addr.capitalize()} đang cần tìm sản phẩm nào để {self_term} tư vấn thêm ạ?"
    labels = " hoặc ".join(f"**{cat}**" for cat in suggestions)
    return (f"Nếu {addr} cần thiết bị phục vụ học tập hoặc làm việc, {self_term} có thể tư vấn {labels} — "
            f"{addr} muốn xem nhóm nào ạ?")


def _off_topic_redirect(query: str, config, addr: str, self_term: str) -> str:
    """Từ chối ngắn gọn và chuyển hướng; không phát sinh thêm LLM call/token."""
    categories = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
    suggestions = _off_topic_suggestions(query, categories)
    labels = ", ".join(f"**{cat}**" for cat in suggestions)
    if is_programming_request(query) and labels:
        return (f"Dạ, {self_term} chuyên tư vấn sản phẩm nên không hỗ trợ viết code chi tiết ạ. "
                f"Nếu {addr} cần thiết bị để học hoặc lập trình, bên {self_term} có {labels}. "
                f"{addr.capitalize()} muốn xem nhóm nào ạ?")
    if labels:
        return (f"Dạ, phần này nằm ngoài phạm vi tư vấn của {self_term} ạ. "
                f"Nếu {addr} đang cần mua sắm, bên {self_term} có thể tư vấn {labels}. "
                f"{addr.capitalize()} quan tâm nhóm nào ạ?")
    return (f"Dạ, phần này nằm ngoài phạm vi tư vấn của {self_term} ạ. "
            f"{addr.capitalize()} đang cần tìm sản phẩm nào?")


def chitchat_node(state: AgentState, config) -> AgentState:
    """Xã giao dùng lời AI; ngoài chủ đề trả mẫu ngắn và quay về mua sắm."""
    intent = state.get("intent", {})
    query = state.get("query", "")
    addr = _addr(state)
    self_term = _self(state)
    if is_off_topic_request(query):
        text = _off_topic_redirect(query, config, addr, self_term)
    else:
        reply = (intent.get("smalltalk_reply") or "").strip()
        if reply:
            # Không có fact card nào để truy nguồn -> câu đáp không được chứa số liệu lạ.
            result = verify_advice(AdviceResult(message=reply, cards=[], assumptions=[], warnings=[]))
            if not is_grounded(result):
                log.warning("chitchat: câu đáp AI dính số lạ -> dùng câu mặc định")
                reply = ""
        text = reply or _chitchat_fallback(addr, self_term)
        if reply:
            categories = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
            if not _has_shopping_pivot(reply, categories):
                text = f"{reply} {_knowledge_pivot(categories, addr, self_term)}"
    log.info("chitchat_node: reply=%r", text[:80])
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "collecting", "question": None,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def policy_node(state: AgentState, config) -> AgentState:
    """Khách hỏi chính sách/vận hành cửa hàng: RAG nhẹ trên tài liệu chính sách đã biên tập.
    LLM soạn lời nhưng số liệu phải truy nguyên về tài liệu; lỗi/bịa -> trả nguyên văn chunk."""
    # Defense-in-depth cho lời gọi trực tiếp/test hoặc checkpoint cũ: cùng một
    # intent vừa policy vừa unsupported phải luôn trả đúng thông báo unsupported.
    if state.get("intent", {}).get("unsupported_product"):
        return unsupported_node(state, config)
    _notify(config, f"{_self(state).capitalize()} đang tra cứu chính sách cửa hàng…")
    query = state.get("query", "")
    # Ngữ cảnh là bắt buộc: khách hỏi "phí lắp đặt như nào" giữa cuộc tư vấn tủ lạnh
    # thì phải trả lời cho tủ lạnh, không được trút ví dụ của nhóm hàng khác.
    intent = state.get("intent", {})
    category = intent.get("category")
    if not category:
        last = state.get("last_products") or []
        if last:
            category = last[0].get("category")
    # history lúc này đã chứa câu hỏi hiện tại (intent_node vừa append) -> bỏ phần tử cuối.
    history = (state.get("history") or [])[:-1]
    text = answer_policy(query, _cfg(config, "llm"), history=history, category=category,
                         addr=_addr(state), self_term=_self(state))
    log.info("policy_node: category=%r reply=%r", category, text[:80])
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "collecting", "question": None,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def meta_inquiry_node(state: AgentState, config) -> AgentState:
    """Khách hỏi ngược lại (meta-inquiry): Giải thích thuật ngữ/lý do, sau đó hỏi lại."""
    intent = state.get("intent", {})
    reply = (intent.get("meta_reply") or "").strip()
    if not reply:
        reply = f"Dạ, {_addr(state)} cần {_self(state)} giải thích thêm về tiêu chí nào ạ?"
    text = reply
    log.info("meta_inquiry_node: reply=%r", text[:80])
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "question": None, "stage": "collecting",
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history,
            "clarify_count": state.get("clarify_count", 0) + 1}


def unsupported_node(state: AgentState, config) -> AgentState:
    """Khách hỏi mặt hàng không kinh doanh: nói thật, gợi ý nhóm liên quan CÓ bán.
    Gợi ý do AI chọn nhưng được đối chiếu với danh mục thật trước khi phát."""
    intent = state.get("intent", {})
    want = intent.get("unsupported_product") or "mặt hàng này"
    cats_db = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
    rel = [c for c in (intent.get("related_categories") or []) if c in cats_db][:3]
    log.info("unsupported_node: want=%r related(validated)=%s", want, rel)
    addr = _addr(state)
    self_term = _self(state)
    if rel:
        sugg = ", ".join(f"**{c}**" for c in rel)
        text = (f"Dạ rất tiếc, bên {self_term} hiện **chưa kinh doanh {want}** ạ. "
                f"Gần với nhu cầu đó, bên {self_term} có: {sugg} — {addr} muốn xem thử nhóm nào không ạ?")
    else:
        sugg = ", ".join(cats_db)
        text = (f"Dạ rất tiếc, bên {self_term} hiện **chưa kinh doanh {want}** ạ. "
                f"Bên {self_term} đang có các nhóm: {sugg}. {addr.capitalize()} quan tâm nhóm nào ạ?")
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "collecting", "question": text,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def detail_node(state: AgentState, config) -> AgentState:
    _notify(config, f"{_self(state).capitalize()} đang tra cứu chi tiết sản phẩm…")
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    row = resolve_product_row(query, last)
    if row is None and state.get("focused_sku"):
        row = next((r for r in last if _sku(r) == state["focused_sku"]), None)
    if row is None:
        row = last[0]
    log.info("detail_node: resolved -> %s", product_display_name(row))
    message, card = answer_detail(row, query, _cfg(config, "llm"), addr=_addr(state), self_term=_self(state))
    # Lần đầu khách xem chi tiết sản phẩm này -> chốt ngay: gỡ trước rào cản phí giao
    # hàng rồi mời sang bước đặt hàng, tránh hỏi lặp lại ở các câu hỏi sâu hơn sau đó.
    if state.get("focused_sku") != _sku(row):
        hook = closing_hook(row.get("category"), float(row.get("price_clean") or 0),
                            addr=_addr(state), self_term=_self(state))
        message = f"{message} {hook}"
        # Bán chéo: ngay lúc khách "chốt máy" (xem chi tiết lần đầu), gợi mở 1 sản phẩm
        # bổ trợ THẬT trong catalog cho combo mua kèm đúng ngữ cảnh ngành hàng.
        cross = cross_sell_suggestion(row.get("category"), float(row.get("price_clean") or 0),
                                      db_path=_cfg(config, "db_path"), exclude_sku=_sku(row))
        if cross:
            message = f"{message} {cross_sell_line(cross, addr=_addr(state), self_term=_self(state))}"
    history = state.get("history", []) + [{"role": "assistant", "content": message}]
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [card.model_dump()], "comparison": None, "assumptions": [], "warnings": [],
            "focused_sku": _sku(row), "history": history}


def confirm_purchase_node(state: AgentState, config) -> AgentState:
    """Khách chốt đơn một máy đang bàn -> ghi nhận vào lịch sử mua hàng của phiên (nền tảng
    cho chăm sóc sau mua) và chốt sổ: gỡ rào phí giao hàng + gợi mở mua kèm 1 lần nữa."""
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    row = resolve_product_row(query, last) if last else None
    if row is None and state.get("focused_sku"):
        row = next((r for r in last if _sku(r) == state["focused_sku"]), None)
    if row is None and last:
        row = last[0]
    addr, self_term = _addr(state), _self(state)
    if row is None:
        text = (f"Dạ {addr} muốn chốt máy nào ạ? {self_term.capitalize()} chưa xác định được "
                f"sản phẩm cụ thể trong hội thoại này.")
        history = state.get("history", []) + [{"role": "assistant", "content": text}]
        return {"response": text, "stage": "recommended", "question": None,
                "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}
    log.info("confirm_purchase_node: resolved -> %s", product_display_name(row))
    name = product_display_name(row)
    price = float(row.get("price_clean") or 0)
    price_txt = format_vnd(int(price)) if price > 0 else "chưa có dữ liệu giá"
    warranty = load_specs(row).get("bảo hành (crawl)")
    entry = {"sku": _sku(row), "name": name, "category": row.get("category"),
             "price": price, "warranty": warranty}
    purchases = [p for p in (state.get("purchase_history") or []) if p.get("sku") != entry["sku"]]
    purchases.append(entry)
    hook = closing_hook(row.get("category"), price, addr=addr, self_term=self_term)
    text = f"Dạ {self_term} đã ghi nhận {addr} chốt {name} (giá {price_txt}, nguồn: catalog) ạ! {hook}"
    cross = cross_sell_suggestion(row.get("category"), price, db_path=_cfg(config, "db_path"),
                                  exclude_sku=entry["sku"])
    if cross:
        text = f"{text} {cross_sell_line(cross, addr=addr, self_term=self_term)}"
    card = build_detail_card(row)
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "recommended", "question": None,
            "cards": [card.model_dump()], "comparison": None, "assumptions": [], "warnings": [],
            "purchase_history": purchases, "focused_sku": entry["sku"], "history": history}


def aftersales_node(state: AgentState, config) -> AgentState:
    """Chăm sóc sau mua (2.7): trả lời bảo hành/chính sách ưu đãi TRA THEO lịch sử mua hàng
    của phiên (do confirm_purchase_node ghi nhận) — không suy diễn cho máy khách chưa chốt."""
    _notify(config, f"{_self(state).capitalize()} đang tra cứu thông tin đơn hàng…")
    addr, self_term = _addr(state), _self(state)
    purchases = state.get("purchase_history") or []
    if not purchases:
        text = (f"Dạ {self_term} chưa thấy đơn hàng nào của {addr} trong phiên tư vấn này ạ. "
                f"{addr.capitalize()} cho {self_term} biết tên/mã máy đã mua để tra cứu bảo hành giúp, "
                f"hoặc gọi tổng đài 1900.232.461 (7:30 - 22:00 mỗi ngày) để được hỗ trợ trực tiếp ạ.")
        history = state.get("history", []) + [{"role": "assistant", "content": text}]
        return {"response": text, "stage": "recommended", "question": None,
                "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}
    last_purchase = purchases[-1]
    query = state.get("query", "")
    history_msgs = (state.get("history") or [])[:-1]
    policy_reply = answer_policy(query, _cfg(config, "llm"), history=history_msgs,
                                 category=last_purchase.get("category"), addr=addr, self_term=self_term)
    warranty_line = ""
    if last_purchase.get("warranty"):
        warranty_line = (f" Riêng {last_purchase['name']} {addr} đã mua, thời hạn bảo hành ghi nhận "
                         f"từ nhà bán là {last_purchase['warranty']} (nguồn: dienmayxanh.com).")
    text = f"Dạ về {last_purchase['name']} {addr} đã đặt:{warranty_line} {policy_reply}"
    cross = cross_sell_suggestion(last_purchase.get("category"), last_purchase.get("price") or 0,
                                  db_path=_cfg(config, "db_path"), exclude_sku=last_purchase.get("sku"))
    if cross:
        text = f"{text} Cho lần mua kế tiếp, {cross_sell_line(cross, addr=addr, self_term=self_term)}"
    log.info("aftersales_node: last_purchase=%s", last_purchase.get("name"))
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "recommended", "question": None,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def retrieval_node(state: AgentState, config) -> AgentState:
    _notify(config, f"{_self(state).capitalize()} đang tìm máy phù hợp trong catalog…")
    intent = state.get("intent", {})
    res = None
    if (intent.get("declines_more_info") and intent.get("category")
            and not intent.get("budget_max") and not intent.get("is_meta_inquiry")):
        # Khách nhờ chọn giúp, chưa chốt ngân sách -> 3 đại diện rẻ/trung/cao thay vì top điểm.
        res = price_spread_products(intent["category"], db_path=_cfg(config, "db_path"))
    elif not intent.get("is_meta_inquiry") and _cfg(config, "llm") is not None:
        # Đường chính cho MỌI truy vấn tìm hàng (đơn giản lẫn ràng buộc thông số):
        # tool SQL — AI soạn SELECT theo schema md, tự sửa tối đa 3 lần; thất bại -> khuôn cũ.
        db_path = _cfg(config, "db_path")
        cat_table = category_table_for(intent["category"], db_path) if intent.get("category") else None
        agent_res = agent_query(_cfg(config, "llm"), state.get("query", ""), intent,
                                cat_table, db_path)
        if agent_res is not None:
            prods = hydrate_rows(agent_res["rows"], db_path) if agent_res.get("rows") else []
            res = {"status": "custom_query" if prods else "no_products_found", 
                   "sql_query": agent_res.get("sql", ""),
                   "total_matches_found": len(prods),
                   "top_3_products": prods[:3], "all_top_k": prods[:5]}
        else:
            log.info("retrieval_node: tool SQL không cho kết quả dùng được -> khuôn cũ")
    if res is None:
        res = search_products(
            query=state.get("query", ""),
            category=intent.get("category"),
            max_price=intent.get("budget_max"),
            brand=intent.get("brand"),
            priority_features=intent.get("priority_features"),
            top_k=5,
            db_path=_cfg(config, "db_path"),
            is_meta_inquiry=intent.get("is_meta_inquiry", False),
        )
    log.info("retrieval_node: status=%s total=%s top3=%s | sql=%s",
             res.get("status"), res.get("total_matches_found"),
             [product_display_name(r) for r in res.get("top_3_products", [])],
             res.get("sql_query"))
    return {"retrieval": res, "last_products": res.get("top_3_products", []), "focused_sku": None}


def advisor_node(state: AgentState, config) -> AgentState:
    _notify(config, f"{_self(state).capitalize()} đang soạn lời tư vấn…")
    intent = state.get("intent", {})
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    status = res.get("status", "exact_match")
    cards = build_cards(rows, intent.get("priority_features", []), self_term=_self(state))
    message, _streamed, warnings = generate_advisor(
        state.get("query", ""), intent, rows, status, _cfg(config, "llm"), cards,
        on_delta=_cfg(config, "on_delta"), addr=_addr(state), self_term=_self(state))
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [c.model_dump() for c in cards], "warnings": warnings,
            "assumptions": list(intent.get("assumptions") or [])}


def compare_node(state: AgentState, config) -> AgentState:
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    intent = state.get("intent", {})
    table = build_comparison(rows, intent.get("priority_features", []), intent.get("budget_max"))
    return {"comparison": table.model_dump() if table else None}


def verify_node(state: AgentState, config) -> AgentState:
    # Guardrail fail-closed đã áp trong generate_advisor. Node này chốt history + là điểm mở rộng.
    history = state.get("history", []) + [{"role": "assistant", "content": state.get("response", "")}]
    return {"history": history}


_COMPILED = None


def get_compiled_graph():
    global _COMPILED
    if _COMPILED is None:
        wf = StateGraph(AgentState)
        wf.add_node("intent_node", intent_node)
        wf.add_node("clarify_node", clarify_node)
        wf.add_node("chitchat_node", chitchat_node)
        wf.add_node("policy_node", policy_node)
        wf.add_node("meta_inquiry_node", meta_inquiry_node)
        wf.add_node("unsupported_node", unsupported_node)
        wf.add_node("detail_node", detail_node)
        wf.add_node("confirm_purchase_node", confirm_purchase_node)
        wf.add_node("aftersales_node", aftersales_node)
        wf.add_node("retrieval_node", retrieval_node)
        wf.add_node("advisor_node", advisor_node)
        wf.add_node("compare_node", compare_node)
        wf.add_node("verify_node", verify_node)
        wf.add_edge(START, "intent_node")
        wf.add_conditional_edges("intent_node", router_edge,
                                 {"clarify": "clarify_node", "detail": "detail_node",
                                  "policy": "policy_node",
                                  "chitchat": "chitchat_node", "meta_inquiry": "meta_inquiry_node",
                                  "unsupported": "unsupported_node", "retrieve": "retrieval_node",
                                  "confirm_purchase": "confirm_purchase_node",
                                  "aftersales": "aftersales_node"})
        wf.add_edge("clarify_node", END)
        wf.add_edge("policy_node", END)
        wf.add_edge("chitchat_node", END)
        wf.add_edge("meta_inquiry_node", END)
        wf.add_edge("unsupported_node", END)
        wf.add_edge("detail_node", END)
        wf.add_edge("confirm_purchase_node", END)
        wf.add_edge("aftersales_node", END)
        wf.add_edge("retrieval_node", "advisor_node")
        wf.add_edge("advisor_node", "compare_node")
        wf.add_edge("compare_node", "verify_node")
        wf.add_edge("verify_node", END)
        _COMPILED = wf.compile(checkpointer=MemorySaver())
    return _COMPILED
