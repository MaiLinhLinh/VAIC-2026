from __future__ import annotations
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agent_core.intent import extract_intent, has_enough_slots
from app.agent_core.retriever import search_products
from app.agent_core.advisor import build_cards, generate_advisor
from app.agent_core.compare import build_comparison
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)
from app.agent_core.presenters import product_display_name


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


def router_edge(state: AgentState) -> str:
    intent = state.get("intent", {})
    if _is_detail_followup(state):
        return "detail"
    if intent.get("needs_clarification") and not has_enough_slots(intent):
        return "clarify"
    return "retrieve"


def clarify_node(state: AgentState, config) -> AgentState:
    intent = state.get("intent", {})
    qs = intent.get("clarification_questions") or ["Bạn cho em thêm thông tin về ngân sách và nhu cầu nhé."]
    questions = "\n".join(f"- {q}" for q in qs)
    cat = intent.get("category") or "sản phẩm"
    text = (f"Chào bạn, để tư vấn chuẩn dòng **{cat}** theo đúng nhu cầu, bạn chia sẻ thêm giúp em:\n\n{questions}")
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "question": qs[0] if qs else None, "stage": "collecting",
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
    message, card = answer_detail(row, query, _cfg(config, "llm"))
    history = state.get("history", []) + [{"role": "assistant", "content": message}]
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [card.model_dump()], "comparison": None, "assumptions": [], "warnings": [],
            "focused_sku": _sku(row), "history": history}


def retrieval_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang tìm máy phù hợp trong catalog…")
    intent = state.get("intent", {})
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
            "cards": [c.model_dump() for c in cards], "warnings": warnings, "assumptions": []}


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
        wf.add_node("detail_node", detail_node)
        wf.add_node("retrieval_node", retrieval_node)
        wf.add_node("advisor_node", advisor_node)
        wf.add_node("compare_node", compare_node)
        wf.add_node("verify_node", verify_node)
        wf.add_edge(START, "intent_node")
        wf.add_conditional_edges("intent_node", router_edge,
                                 {"clarify": "clarify_node", "detail": "detail_node",
                                  "retrieve": "retrieval_node"})
        wf.add_edge("clarify_node", END)
        wf.add_edge("detail_node", END)
        wf.add_edge("retrieval_node", "advisor_node")
        wf.add_edge("advisor_node", "compare_node")
        wf.add_edge("compare_node", "verify_node")
        wf.add_edge("verify_node", END)
        _COMPILED = wf.compile(checkpointer=MemorySaver())
    return _COMPILED
