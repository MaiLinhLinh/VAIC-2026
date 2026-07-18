from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agent_core.intent import extract_intent, has_enough_slots, kw_declines
from app.agent_core.retriever import search_products, price_spread_products, get_catalog_metadata
from app.agent_core.advisor import build_cards, generate_advisor
from app.agent_core.compare import build_comparison
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)
from app.agent_core.presenters import product_display_name
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


def _cfg(config, key, default=None):
    return (config or {}).get("configurable", {}).get(key, default)


def _notify(config, text: str) -> None:
    cb = _cfg(config, "on_status")
    if cb:
        cb(text)


def _sku(row: Dict[str, Any]) -> str:
    return str(row.get("model_code") or row.get("sku") or product_display_name(row))


def intent_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang đọc yêu cầu của anh/chị…")
    query = state.get("query", "")
    history = list(state.get("history", []))
    intent = extract_intent(query, history, _cfg(config, "llm"), _cfg(config, "db_path"))
    # Lưới dự phòng: keyword bắt được từ chối thì tin, kể cả khi LLM bỏ sót.
    if kw_declines(query):
        intent["declines_more_info"] = True
    log.info("intent_node: query=%r -> category=%r budget_max=%s brand=%r feats=%s "
             "assumptions=%s declines=%s needs_clarification=%s meta=%s",
             query, intent.get("category"), intent.get("budget_max"), intent.get("brand"),
             intent.get("priority_features"), intent.get("assumptions"),
             intent.get("declines_more_info"), intent.get("needs_clarification"),
             intent.get("is_meta_inquiry"))
    history = history + [{"role": "user", "content": query}]
    return {"intent": intent, "history": history}


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
    count = state.get("clarify_count", 0)
    # Luật cứng: ngành hàng và NGÂN SÁCH là bắt buộc trước khi đề xuất —
    # không phó mặc cho LLM quyết needs_clarification (nó hay bỏ qua ngân sách).
    missing_required = not intent.get("category") or not intent.get("budget_max")
    declines = bool(intent.get("declines_more_info"))
    if _is_detail_followup(state):
        route = "detail"
    elif intent.get("is_chitchat"):
        # Xã giao/ngoài chủ đề: đáp thân thiện rồi lái về mua sắm, không đụng catalog.
        route = "chitchat"
    elif intent.get("is_meta_inquiry"):
        route = "retrieve"
    elif intent.get("unsupported_product"):
        # Khách hỏi mặt hàng không kinh doanh -> nói thật + gợi ý nhóm liên quan có bán.
        route = "unsupported"
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
    # Câu hỏi do AI soạn theo bối cảnh khách kể — dùng nguyên văn; luật chỉ vá khi thiếu.
    qs = [q.strip() for q in (intent.get("clarification_questions") or []) if q.strip()][:2]
    if not cat and not qs:
        cats = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
        qs = ["Bên em đang có: " + ", ".join(cats) + ". Anh/chị đang cần nhóm sản phẩm nào ạ?"]
    # Luật "chốt sổ": đây là câu hỏi cuối của quota mà ngân sách vẫn trống -> phải phủ ngân sách.
    _money = ("ngân sách", "giá", "bao nhiêu", "tiền", "triệu", "tầm")
    if (cat and not intent.get("budget_max") and count >= _MAX_CLARIFY - 1
            and not any(w in q.lower() for q in qs for w in _money)):
        qs = (qs + ["Anh/chị dự tính ngân sách khoảng bao nhiêu ạ?"])[-2:]
    # Chỉ chào ở lượt trợ lý mở lời đầu tiên của phiên; các lượt sau vào thẳng câu hỏi.
    greeted = any(m.get("role") == "assistant" for m in state.get("history", []))
    if greeted:
        opener = "Dạ, em cần thêm chút thông tin để chọn đúng máy cho mình:"
    elif cat:
        opener = f"Chào bạn! Để tư vấn chuẩn dòng **{cat}** theo đúng nhu cầu, bạn chia sẻ thêm giúp em:"
    else:
        opener = "Chào bạn! Để tư vấn đúng nhu cầu, bạn chia sẻ thêm giúp em:"
    text = opener + "\n\n" + "\n".join(f"- {q}" for q in qs)
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "question": qs[0] if qs else None, "stage": "collecting",
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history,
            "clarify_count": state.get("clarify_count", 0) + 1}


_CHITCHAT_FALLBACK = ("Dạ em luôn sẵn sàng trò chuyện ạ 😊 Em là trợ lý tư vấn điện máy — "
                      "anh/chị đang cần tìm sản phẩm nào để em giúp mình chọn nhanh nhất ạ?")


def chitchat_node(state: AgentState, config) -> AgentState:
    """Khách nhắn xã giao/ngoài chủ đề: dùng câu đáp AI soạn (đã kiểm không dính số lạ),
    luôn kết bằng lời mời quay về nhu cầu mua sắm."""
    intent = state.get("intent", {})
    reply = (intent.get("smalltalk_reply") or "").strip()
    if reply:
        # Không có fact card nào để truy nguồn -> câu đáp không được chứa số liệu lạ.
        result = verify_advice(AdviceResult(message=reply, cards=[], assumptions=[], warnings=[]))
        if not is_grounded(result):
            log.warning("chitchat: câu đáp AI dính số lạ -> dùng câu mặc định")
            reply = ""
    text = reply or _CHITCHAT_FALLBACK
    log.info("chitchat_node: reply=%r", text[:80])
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "collecting", "question": None,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def unsupported_node(state: AgentState, config) -> AgentState:
    """Khách hỏi mặt hàng không kinh doanh: nói thật, gợi ý nhóm liên quan CÓ bán.
    Gợi ý do AI chọn nhưng được đối chiếu với danh mục thật trước khi phát."""
    intent = state.get("intent", {})
    want = intent.get("unsupported_product") or "mặt hàng này"
    cats_db = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
    rel = [c for c in (intent.get("related_categories") or []) if c in cats_db][:3]
    log.info("unsupported_node: want=%r related(validated)=%s", want, rel)
    if rel:
        sugg = ", ".join(f"**{c}**" for c in rel)
        text = (f"Dạ rất tiếc, bên em hiện **chưa kinh doanh {want}** ạ. "
                f"Gần với nhu cầu đó, bên em có: {sugg} — anh/chị muốn xem thử nhóm nào không ạ?")
    else:
        sugg = ", ".join(cats_db)
        text = (f"Dạ rất tiếc, bên em hiện **chưa kinh doanh {want}** ạ. "
                f"Bên em đang có các nhóm: {sugg}. Anh/chị quan tâm nhóm nào ạ?")
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "stage": "collecting", "question": text,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def detail_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang tra cứu chi tiết sản phẩm…")
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    row = resolve_product_row(query, last)
    if row is None and state.get("focused_sku"):
        row = next((r for r in last if _sku(r) == state["focused_sku"]), None)
    if row is None:
        row = last[0]
    log.info("detail_node: resolved -> %s", product_display_name(row))
    message, card = answer_detail(row, query, _cfg(config, "llm"))
    history = state.get("history", []) + [{"role": "assistant", "content": message}]
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [card.model_dump()], "comparison": None, "assumptions": [], "warnings": [],
            "focused_sku": _sku(row), "history": history}


def retrieval_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang tìm máy phù hợp trong catalog…")
    intent = state.get("intent", {})
    if (intent.get("declines_more_info") and intent.get("category")
            and not intent.get("budget_max") and not intent.get("is_meta_inquiry")):
        # Khách nhờ chọn giúp, chưa chốt ngân sách -> 3 đại diện rẻ/trung/cao thay vì top điểm.
        res = price_spread_products(intent["category"], db_path=_cfg(config, "db_path"))
    else:
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
    _notify(config, "Em đang soạn lời tư vấn…")
    intent = state.get("intent", {})
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    status = res.get("status", "exact_match")
    cards = build_cards(rows, intent.get("priority_features", []))
    message, _streamed, warnings = generate_advisor(
        state.get("query", ""), intent, rows, status, _cfg(config, "llm"), cards,
        on_delta=_cfg(config, "on_delta"))
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [c.model_dump() for c in cards], "warnings": warnings,
            "assumptions": list(intent.get("assumptions") or [])}


def compare_node(state: AgentState, config) -> AgentState:
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    intent = state.get("intent", {})
    table = build_comparison(rows, intent.get("priority_features", []))
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
        wf.add_node("unsupported_node", unsupported_node)
        wf.add_node("detail_node", detail_node)
        wf.add_node("retrieval_node", retrieval_node)
        wf.add_node("advisor_node", advisor_node)
        wf.add_node("compare_node", compare_node)
        wf.add_node("verify_node", verify_node)
        wf.add_edge(START, "intent_node")
        wf.add_conditional_edges("intent_node", router_edge,
                                 {"clarify": "clarify_node", "detail": "detail_node",
                                  "chitchat": "chitchat_node", "unsupported": "unsupported_node",
                                  "retrieve": "retrieval_node"})
        wf.add_edge("clarify_node", END)
        wf.add_edge("chitchat_node", END)
        wf.add_edge("unsupported_node", END)
        wf.add_edge("detail_node", END)
        wf.add_edge("retrieval_node", "advisor_node")
        wf.add_edge("advisor_node", "compare_node")
        wf.add_edge("compare_node", "verify_node")
        wf.add_edge("verify_node", END)
        _COMPILED = wf.compile(checkpointer=MemorySaver())
    return _COMPILED
