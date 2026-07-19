from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agent_core.intent import extract_intent, has_enough_slots, kw_declines, kw_unknown_answer
from app.agent_core.retriever import (search_products, price_spread_products, get_catalog_metadata,
                                       category_table_for, hydrate_rows, retrieve_scored,
                                       catalog_field_values)
from app.agent_core.advisor import build_cards, generate_advisor
from app.agent_core.compare import build_comparison
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)
from app.agent_core.slots import (spec_slot_columns, update_slots, reached_threshold,
                                   count_filled, count_touched, slots_summary,
                                   next_description_question)
from app.agent_core.presenters import product_display_name, build_detail_card
from app.advice.provenance import facts_for_llm
from app.advice.verify import verify_advice, is_grounded
from app.nlu.preprocess import strip_accents
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
    last_category: Optional[str]
    slots: List[Dict[str, Any]]      # slot đặc thù ngành đang thu thập (theo cột DB)
    next_question: Optional[str]     # câu hỏi theo thứ tự nhu cầu -> giá -> cột DB
    slot_stage: Optional[str]        # None | "await_compare_confirm"
    offered_touched: int             # mức 'touched' lúc gần nhất mời so sánh (chống mời lặp)
    offered_clarify_count: int       # số lượt hỏi lúc gần nhất mời so sánh
    top_n: int                       # số sản phẩm khách muốn khoanh để so sánh
    asked_usage: bool                # đã hỏi/đã có: mua cho ai, dùng làm gì
    asked_budget: bool               # đã hỏi/đã có ngân sách
    priority_question_pending: bool  # còn phải hỏi nhu cầu/ngân sách trước slot DB
    awaiting_description_fields: bool  # lượt trước vừa hỏi nhóm 2-3 trường mô tả
    comparison_followup: bool          # so sánh lại một nhóm trong last_products


def _cfg(config, key, default=None):
    return (config or {}).get("configurable", {}).get(key, default)


def _notify(config, text: str) -> None:
    cb = _cfg(config, "on_status")
    if cb:
        cb(text)


def _sku(row: Dict[str, Any]) -> str:
    return str(row.get("model_code") or row.get("sku") or product_display_name(row))


_USAGE_EVIDENCE = re.compile(
    r"\b(?:cho\s+(?:ai|be|con|bo|me|gia dinh|nhan vien)|"
    r"dung\s+(?:de|cho)|de\s+(?:hoc|lam|choi|in|giat|say|rua|bao quan)|"
    r"van phong|gia dinh|hoc tap|kinh doanh|choi game|do hoa)\b"
)


def _has_usage_context(query: str) -> bool:
    return bool(_USAGE_EVIDENCE.search(strip_accents((query or "").lower())))


def _merge_carried_intent(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    """Giữ tiêu chí đã nói ở lượt trước khi vẫn đang trong cùng một ngành."""
    old_cat, new_cat = previous.get("category"), current.get("category")
    if old_cat and new_cat and old_cat != new_cat:
        return current
    merged = dict(current)
    if not merged.get("category"):
        merged["category"] = old_cat
    for key in ("budget_max", "brand"):
        if merged.get(key) is None and previous.get(key) is not None:
            merged[key] = previous[key]
    merged["priority_features"] = list(dict.fromkeys(
        [*(previous.get("priority_features") or []), *(merged.get("priority_features") or [])]
    ))
    merged["required_features"] = list(dict.fromkeys(
        [*(previous.get("required_features") or []), *(merged.get("required_features") or [])]
    ))
    return merged


def _context_comparison_rows(query: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lọc các kết quả vừa xem theo hãng/model khi khách nói 'so sánh SingPC'."""
    flat = strip_accents((query or "").lower())
    if "so sanh" not in flat or not rows:
        return []
    selected = []
    for row in rows:
        brand = strip_accents(str(row.get("brand") or "").lower()).strip()
        code = strip_accents(str(row.get("model_code") or row.get("sku") or "").lower()).strip()
        if (brand and len(brand) >= 2 and brand in flat) or (code and code in flat):
            selected.append(row)
    # "so sánh lại" không chỉ rõ hãng/model -> giữ toàn bộ danh sách gần nhất.
    return selected or (list(rows) if "so sanh lai" in flat else [])


def _sanitize_required_features(intent: Dict[str, Any], query: str) -> Dict[str, Any]:
    """Tính năng model suy luận nhưng khách không nói tới chỉ được dùng làm ưu tiên mềm."""
    cleaned = dict(intent)
    query_tokens = set(re.findall(r"\w+", strip_accents((query or "").lower())))
    kept, demoted = [], []
    for feature in cleaned.get("required_features") or []:
        tokens = [token for token in re.findall(r"\w+", strip_accents(str(feature).lower()))
                  if len(token) > 1]
        (kept if tokens and all(token in query_tokens for token in tokens) else demoted).append(feature)
    cleaned["required_features"] = list(dict.fromkeys(kept))
    cleaned["priority_features"] = list(dict.fromkeys([
        *(cleaned.get("priority_features") or []), *demoted,
    ]))
    return cleaned


def intent_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang đọc yêu cầu của anh/chị…")
    query = state.get("query", "")
    history = list(state.get("history", []))
    previous_intent = state.get("intent", {}) or {}
    intent = extract_intent(query, history, _cfg(config, "llm"), _cfg(config, "db_path"))
    intent = _sanitize_required_features(intent, query)
    context_rows = _context_comparison_rows(query, state.get("last_products", []) or [])
    if context_rows:
        # Ngữ cảnh kết quả gần nhất thắng dự đoán category mới của model.
        intent["category"] = context_rows[0].get("category") or previous_intent.get("category")
        intent["brand"] = None
        intent["wants_comparison"] = True
        intent["needs_clarification"] = False
        intent["is_meta_inquiry"] = False
    # Câu hỏi giải thích/tư vấn một tiêu chí chỉ là lượt chen ngang. Không biến các từ trong
    # câu hỏi (vd. "băng tần nào tốt cho người già") thành priority feature mua hàng mới.
    interrupting_question = (
        asks_for_explanation(query) or asks_for_criterion_advice(query)
        or asks_for_criterion_options(query)
    ) and not asks_for_product_recommendation(query)
    if interrupting_question:
        intent["priority_features"] = []
        intent["required_features"] = []
        intent["wants_comparison"] = False
        intent["declines_more_info"] = False
    # Lưới dự phòng: keyword bắt được từ chối thì tin, kể cả khi LLM bỏ sót.
    if kw_declines(query):
        intent["declines_more_info"] = True
    elif kw_unknown_answer(query):
        # "Không biết" chỉ bỏ qua câu hiện tại; không đồng nghĩa "đừng hỏi nữa, chọn luôn".
        intent["declines_more_info"] = False
    raw_new_cat = intent.get("category")
    last_cat = state.get("last_category")
    switched = bool(raw_new_cat and last_cat and raw_new_cat != last_cat)
    if not switched:
        intent = _merge_carried_intent(intent, previous_intent)
    new_cat = intent.get("category")
    out: Dict[str, Any] = {"intent": intent, "comparison_followup": bool(context_rows),
                           "history": history + [{"role": "user", "content": query}]}
    # Đổi NGÀNH HÀNG = nhu cầu mới -> reset toàn bộ trạng thái thu thập slot cho ngành mới.
    slots = [] if switched else list(state.get("slots") or [])
    asked_usage = False if switched else bool(state.get("asked_usage"))
    asked_budget = False if switched else bool(state.get("asked_budget"))
    was_awaiting_description = False if switched else bool(state.get("awaiting_description_fields"))
    if switched:
        out.update({"slot_stage": None, "offered_touched": -1,
                    "offered_clarify_count": -1, "top_n": 3, "clarify_count": 0})
    if new_cat:
        out["last_category"] = new_cat

    # Chỉ gọi LLM slot khi lượt trước THỰC SỰ đã hỏi nhóm trường mô tả. Hai câu đầu
    # (nhu cầu, ngân sách) và việc chọn câu hỏi tiếp theo đều không cần thêm một LLM call.
    side = (intent.get("is_chitchat") or intent.get("is_meta_inquiry")
            or intent.get("unsupported_product") or interrupting_question)
    if new_cat and not side:
        db_path = _cfg(config, "db_path")
        cat_table = category_table_for(new_cat, db_path)
        cols = spec_slot_columns(cat_table, db_path) if cat_table else []
        should_extract_slots = (
            was_awaiting_description
            and state.get("slot_stage") != "await_compare_confirm"
            and not ends_with_question(query)
        )
        if should_extract_slots:
            res = update_slots(_cfg(config, "llm"), query, history, new_cat, cols, slots)
        else:
            res = {"slots": slots, **next_description_question(cols, slots)}
        out["slots"] = res["slots"]
        # Thứ tự thu thập cố định: bối cảnh dùng -> ngân sách -> cột thông số có thật trong DB.
        # Hai câu đầu là ngữ cảnh mua hàng; từ câu thứ ba hệ thống gộp 2-3 cột hợp lệ
        # của search_description mà không nhờ LLM chọn câu hỏi.
        if not cols:
            # DB tối giản/test adapter không có bảng ngành: giữ hành vi tìm trực tiếp.
            out["next_question"] = None
            out["priority_question_pending"] = False
            out["awaiting_description_fields"] = False
        elif _has_usage_context(query):
            asked_usage = True
        if cols and intent.get("budget_max") is not None:
            asked_budget = True
        if cols and not asked_usage:
            out["next_question"] = (
                f"Anh/chị mua {new_cat} cho ai và chủ yếu dùng để làm gì ạ?"
            )
            asked_usage = True
            out["priority_question_pending"] = True
            out["awaiting_description_fields"] = False
        elif cols and not asked_budget:
            out["next_question"] = "Ngân sách dự kiến của anh/chị khoảng bao nhiêu ạ?"
            asked_budget = True
            out["priority_question_pending"] = True
            out["awaiting_description_fields"] = False
        elif cols:
            out["next_question"] = res["next_question"]
            out["priority_question_pending"] = False
            out["awaiting_description_fields"] = bool(res["next_question"])
    else:
        out["slots"] = slots
        out["priority_question_pending"] = False
        out["awaiting_description_fields"] = was_awaiting_description
    out["asked_usage"] = asked_usage
    out["asked_budget"] = asked_budget

    log.info("intent_node: query=%r -> category=%r declines=%s meta=%s chitchat=%s | "
             "slots filled=%d touched=%d (switched=%s)",
             query, new_cat, intent.get("declines_more_info"), intent.get("is_meta_inquiry"),
             intent.get("is_chitchat"), count_filled(out.get("slots") or []),
             count_touched(out.get("slots") or []), switched)
    return out


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


# Trả lời câu "muốn xem top mấy để so sánh?": (đồng ý, N) | (từ chối) | (chưa rõ = thêm tiêu chí).
# Chỉ nhận "yes" khi RÕ RÀNG (có số hoặc từ đồng ý dứt khoát) — tránh nhầm câu bổ sung tiêu chí.
_COMPARE_YES = ("dong y", "so sanh di", "xem di", "chot", "vang a", " co a", "okie", " ok ", "oke")
_COMPARE_NO = ("khong", "chua", "thoi", "de sau", "chua can", "tu tu")


def parse_compare_reply(query: str) -> tuple[str, int]:
    flat = " " + strip_accents((query or "").lower()).strip() + " "
    import re as _re
    # Số kèm đơn vị tiền là NGÂN SÁCH, không phải "top N" -> bỏ qua khi tìm số top.
    money = _re.search(r"\d+\s*(trieu|tr|cu|trd|k|nghin|ngan|dong|d)\b", flat)
    m = _re.search(r"\b(\d+)\b", flat)
    if m and not money and int(m.group(1)) <= 10:
        return "yes", max(2, min(4, int(m.group(1))))
    if any(k in flat for k in _COMPARE_NO):
        return "no", 3
    if any(k in flat for k in _COMPARE_YES):
        return "yes", 3
    return "unclear", 3   # khách nói thêm tiêu chí -> coi như bổ sung slot, mời lại


# Luật cứng: lượt khách KẾT THÚC bằng câu hỏi -> trả lời câu hỏi, KHÔNG đưa sản phẩm/so sánh.
# Đuôi câu hỏi tiếng Việt (đã bỏ dấu) dùng khi khách không gõ dấu "?".
_QUESTION_TAILS = ("khong", "ko", "sao", "gi", "nao", "nhi", "the nao", "bao nhieu",
                   "bao lau", "o dau", "la gi", "duoc chu", "phai khong")
# Ngoại lệ: câu hỏi mà bản thân nó là lời XIN ĐỀ XUẤT -> đề xuất chính là câu trả lời.
_RECO_REQUEST_KW = ("goi y", "tu van", "de xuat", "chon giup", "chon dum", "nen mua",
                    "nen chon", "nen lay", "nao tot", "nao re", "nao ben", "nao phu hop",
                    "nao dang mua", "mau nao", "loai nao", "co mau", "co loai", "recommend")
_EXPLANATION_KW = ("la gi", "la sao", "nghia la gi", "co nghia la", "dung de lam gi", "khac gi")


def ends_with_question(query: str) -> bool:
    raw = (query or "").strip().rstrip(".!…~ ")
    if raw.endswith("?"):
        return True
    flat = strip_accents(raw.lower()).rstrip("?!. ")
    return any(flat.endswith(t) for t in _QUESTION_TAILS)


def asks_for_recommendation(query: str) -> bool:
    flat = strip_accents((query or "").lower())
    return any(k in flat for k in _RECO_REQUEST_KW)


def asks_for_product_recommendation(query: str) -> bool:
    """Phân biệt xin chọn sản phẩm với hỏi giá trị nào của một thông số là phù hợp."""
    flat = strip_accents((query or "").lower())
    product_words = ("san pham", "may", "mau", "model", "dong", "loai", "cai nao", "con nao")
    return asks_for_recommendation(query) and any(word in flat for word in product_words)


def asks_for_criterion_advice(query: str) -> bool:
    """VD: 'băng tần nào thì tốt cho người già?' phải được giải đáp, không kích hoạt top 3."""
    if not ends_with_question(query):
        return False
    flat = strip_accents((query or "").lower())
    asks_which_is_suitable = any(
        phrase in flat for phrase in ("nao thi tot", "nao tot cho", "nao phu hop cho", "nen chon muc nao")
    )
    return asks_which_is_suitable and not asks_for_product_recommendation(query)


def asks_for_criterion_options(query: str) -> bool:
    """VD: 'có các công nghệ nào?' là hỏi giá trị của trường DB, không phải xin top máy."""
    if not ends_with_question(query):
        return False
    flat = strip_accents((query or "").lower())
    option_form = any(phrase in flat for phrase in (
        "co cac", "co nhung", "gom nhung gi", "bao gom nhung gi", "cac loai nao"
    ))
    criterion_words = (
        "cong nghe", "tinh nang", "tien ich", "che do", "ket noi", "bang tan",
        "tai trong", "dung tich", "cong suat", "do phan giai", "loai san pham",
    )
    return option_form and any(word in flat for word in criterion_words)


def asks_for_explanation(query: str) -> bool:
    """Deterministic guard for concept questions when intent classification is imperfect."""
    flat = strip_accents((query or "").lower())
    return ends_with_question(query) and any(k in flat for k in _EXPLANATION_KW)


def router_edge(state: AgentState) -> str:
    intent = state.get("intent", {})
    query = state.get("query", "")
    slots = state.get("slots") or []
    declines = bool(intent.get("declines_more_info"))
    wants_reco = asks_for_recommendation(query) or intent.get("wants_comparison")
    # A concept question must win over compare thresholds and stale recommendation state.
    # Otherwise "tải trọng là gì?" can be interpreted as enough signal to retrieve products.
    if ((asks_for_explanation(query) and not asks_for_product_recommendation(query))
            or asks_for_criterion_advice(query) or asks_for_criterion_options(query)):
        route = "question"
    elif state.get("comparison_followup"):
        route = "compare_followup"
    elif _is_detail_followup(state):
        route = "detail"
    elif intent.get("is_chitchat"):
        route = "chitchat"
    elif intent.get("is_meta_inquiry"):
        route = "meta_inquiry"
    elif intent.get("unsupported_product"):
        route = "unsupported"
    elif not intent.get("category"):
        # Luật thép: chưa rõ ngành hàng -> không bao giờ đề xuất, hỏi ngành trước.
        route = "clarify"
    elif state.get("slot_stage") == "await_compare_confirm":
        # Đang chờ khách trả lời "muốn xem top mấy?".
        verdict = parse_compare_reply(query)[0]
        if verdict == "yes":
            route = "retrieve"                # đồng ý (kèm số top N) -> đề xuất
        elif verdict == "no":
            route = "clarify"                 # chưa muốn so sánh -> hỏi thêm slot để thu hẹp
        else:
            route = "confirm_compare"         # bổ sung tiêu chí -> cập nhật rồi mời lại (giữ chờ)
    elif state.get("priority_question_pending") and not declines:
        # Không nhảy sang so sánh/đề xuất trước hai câu ưu tiên: nhu cầu rồi ngân sách.
        route = "clarify"
    elif (state.get("clarify_count", 0) >= 3
          and state.get("clarify_count", 0) > state.get("offered_clarify_count", -1)):
        # Sau khoảng 3 lượt hỏi (kể cả khách nói không biết) thì mời khoanh top để so sánh.
        # question/meta nodes không tăng clarify_count nên giải đáp chen ngang không bị tính.
        route = "confirm_compare"
    elif reached_threshold(slots) and state.get("clarify_count", 0) >= 3:
        # Đủ ngưỡng slot -> BẮT BUỘC hỏi "top mấy?" trước khi so sánh (kể cả khi khách có vẻ
        # xin đề xuất — bước xác nhận là chốt chặn). Mời lại chỉ khi có thông tin MỚI (chống lặp);
        # đã mời ở mức này rồi (khách vừa từ chối) -> hỏi thêm slot; hết slot để hỏi -> đề xuất.
        if count_touched(slots) > state.get("offered_touched", -1):
            route = "confirm_compare"
        elif state.get("next_question"):
            route = "clarify"
        else:
            route = "retrieve"
    elif declines or wants_reco:
        # CHƯA đủ ngưỡng nhưng khách sốt ruột xin gợi ý/so sánh, hoặc từ chối bổ sung -> đề xuất ngay.
        route = "retrieve"
    else:
        # Chưa đủ ngưỡng: còn câu hỏi thì hỏi tiếp; hết câu hỏi (không khai thác thêm được) -> đề xuất.
        route = "clarify" if state.get("next_question") else "retrieve"
    # Luật cứng cuối: khách kết thúc bằng CÂU HỎI -> trả lời câu hỏi trước (trừ khi là lời xin đề xuất),
    # và trừ khi đang ở bước xác nhận so sánh (câu "top mấy?" của khách cũng là dạng câu hỏi).
    if (route in ("retrieve", "clarify") and state.get("slot_stage") != "await_compare_confirm"
            and ends_with_question(query) and not asks_for_product_recommendation(query)):
        route = "question"
    log.info("router: -> %s (declines=%s, slot_stage=%r, filled=%d, touched=%d, "
             "offered=%d, next_q=%s, ends_q=%s)",
             route, declines, state.get("slot_stage"), count_filled(slots), count_touched(slots),
             state.get("offered_touched", -1), bool(state.get("next_question")),
             ends_with_question(query))
    return route


def clarify_node(state: AgentState, config) -> AgentState:
    intent = state.get("intent", {})
    cat = intent.get("category")
    # Ưu tiên câu hỏi SLOT do AI soạn theo cột ngành (thu thập nhu cầu đặc thù);
    # thiếu thì rơi về câu hỏi làm rõ chung của intent, cuối cùng là câu gợi mở mặc định.
    nq = state.get("next_question")
    qs = [nq] if nq else [q.strip() for q in (intent.get("clarification_questions") or []) if q.strip()][:2]
    if not cat and not qs:
        cats = get_catalog_metadata(_cfg(config, "db_path"))["categories"]
        qs = ["Bên em đang có: " + ", ".join(cats) + ". Anh/chị đang cần nhóm sản phẩm nào ạ?"]
    elif cat and not qs:
        qs = [f"Anh/chị cần {cat} cho nhu cầu hay mục đích gì ạ?"]
        if not intent.get("budget_max"):
            qs.append("Ngân sách dự kiến của mình khoảng bao nhiêu ạ?")
    greeted = any(m.get("role") == "assistant" for m in state.get("history", []))
    transition = intent.get("transition_message")
    if transition:
        opener = transition
    elif greeted:
        opener = "Dạ, em cần thêm chút thông tin để chọn đúng máy cho mình:"
    elif cat:
        opener = f"Chào bạn! Để tư vấn chuẩn dòng **{cat}** theo đúng nhu cầu, bạn chia sẻ thêm giúp em:"
    else:
        opener = "Chào bạn! Để tư vấn đúng nhu cầu, bạn chia sẻ thêm giúp em:"
    text = opener + "\n\n" + "\n".join(f"- {q}" for q in qs)
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    out: Dict[str, Any] = {"response": text, "question": qs[0] if qs else None, "stage": "collecting",
                           "cards": [], "comparison": None, "assumptions": [], "warnings": [],
                           "history": history, "clarify_count": state.get("clarify_count", 0) + 1}
    # Khách vừa từ chối so sánh -> thoát trạng thái chờ, tiếp tục thu thập.
    if state.get("slot_stage") == "await_compare_confirm":
        out["slot_stage"] = None
    return out


def confirm_compare_node(state: AgentState, config) -> AgentState:
    """Đủ ngưỡng slot -> nhắc lại nhu cầu đã nắm và HỎI khách muốn khoanh top mấy để so sánh.
    Chưa đề xuất gì cho tới khi khách đồng ý."""
    slots = state.get("slots") or []
    summ = slots_summary(slots)
    lead = f"Dạ em đã nắm được: {summ}.\n\n" if summ else ""
    text = (lead + "Anh/chị muốn em khoanh vùng **top mấy sản phẩm** phù hợp nhất để so sánh giúp mình ạ? "
            "(ví dụ: top 3) — hoặc mình cứ nói thêm tiêu chí nếu muốn thu hẹp nữa nhé.")
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    log.info("confirm_compare_node: mời so sánh (filled=%d touched=%d)",
             count_filled(slots), count_touched(slots))
    return {"response": text, "question": text, "stage": "collecting",
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history,
            "slot_stage": "await_compare_confirm", "offered_touched": count_touched(slots),
            "offered_clarify_count": state.get("clarify_count", 0),
            "awaiting_description_fields": False}


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


def meta_inquiry_node(state: AgentState, config) -> AgentState:
    """Khách hỏi ngược lại (meta-inquiry): Giải thích thuật ngữ/lý do, sau đó hỏi lại."""
    intent = state.get("intent", {})
    reply = (intent.get("meta_reply") or "").strip()
    if not reply:
        reply = "Dạ, anh/chị cần em giải thích thêm về tiêu chí nào ạ?"
    text = reply
    log.info("meta_inquiry_node: reply=%r", text[:80])
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "question": None, "stage": "collecting",
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history,
            "clarify_count": state.get("clarify_count", 0)}


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


_QUESTION_SYSTEM = (
    "Bạn là nhân viên tư vấn điện máy thân thiện. Khách đang HỎI — nhiệm vụ duy nhất của bạn "
    "là TRẢ LỜI đúng câu hỏi đó, ngắn gọn, dễ hiểu; TUYỆT ĐỐI không giới thiệu/đề xuất sản phẩm "
    "mới trong lượt này.\n"
    "- Số liệu (giá, thông số) CHỈ được lấy từ FACTS; không có trong FACTS thì nói thẳng "
    "'dạ em chưa có dữ liệu về ... ạ'. Khái niệm/công nghệ giải thích bằng lời, không kèm số tự chế.\n"
    "- Tồn kho, khuyến mãi, chính sách cửa hàng: luôn trả lời 'em chưa có dữ liệu'.\n"
    "- Giải thích thuật ngữ theo đúng NGÀNH HÀNG ĐANG TƯ VẤN; không dùng nghĩa máy móc chung nếu ngữ cảnh "
    "đã xác định một loại sản phẩm cụ thể.\n"
    "- Kết thúc bằng MỘT câu mời khách hỏi tiếp hoặc cho biết thêm nhu cầu, không ép mua."
)

_QUESTION_FALLBACK = ("Dạ câu này em chưa có đủ dữ liệu để trả lời chính xác ạ. "
                      "Anh/chị cứ hỏi thêm hoặc cho em biết nhu cầu để em hỗ trợ tiếp nhé!")


def _known_concept_answer(category: str, query: str) -> str:
    """High-confidence definitions that must not depend on generative wording or invented numbers."""
    cat = strip_accents((category or "").lower())
    flat = strip_accents((query or "").lower())
    if cat == "may giat" and "tai trong" in flat and asks_for_explanation(query):
        return (
            "Tải trọng máy giặt là khối lượng quần áo khô tối đa mà máy được thiết kế "
            "để giặt trong một lần, thường được ghi bằng kg. Đây là khối lượng quần áo "
            "trước khi cho nước vào máy, không phải khối lượng quần áo ướt."
        )
    return ""


def question_node(state: AgentState, config) -> AgentState:
    """Khách kết thúc lượt bằng câu hỏi: trả lời câu hỏi (grounded trên các máy đang tư vấn
    nếu có), không đưa sản phẩm mới, không bảng so sánh."""
    _notify(config, "Em đang trả lời câu hỏi của anh/chị…")
    query = state.get("query", "")
    intent = state.get("intent", {})
    last = state.get("last_products", []) or []
    cards = [build_detail_card(r) for r in last[:3]]
    facts = facts_for_llm(cards) if cards else "(chưa có sản phẩm nào trong ngữ cảnh)"
    active_category = intent.get("category") or state.get("last_category") or "chưa xác định"
    user = (f"NGÀNH HÀNG ĐANG TƯ VẤN: {active_category}\n\nFACTS:\n{facts}\n\n"
            f"Câu hỏi của khách: \"{query}\"\n\n"
            "Trả lời khách theo đúng quy tắc.")
    llm = _cfg(config, "llm")
    message = _known_concept_answer(active_category, query)
    field_values = (catalog_field_values(active_category, query, _cfg(config, "db_path"))
                    if asks_for_criterion_options(query) else {})
    catalog_grounded = bool(field_values)
    if field_values:
        lines = [f"- **{field}**: " + "; ".join(values) for field, values in field_values.items()]
        message = ("Dạ, theo dữ liệu sản phẩm hiện có, bên em có các lựa chọn sau:\n\n"
                   + "\n".join(lines))
    if not message and llm is not None:
        try:
            message = llm.complete_text(_QUESTION_SYSTEM, user)
        except Exception as e:
            log.warning("question: LLM lỗi (%s)", e)
    if message and not catalog_grounded:
        result = verify_advice(AdviceResult(message=message, cards=cards, assumptions=[], warnings=[]))
        if not is_grounded(result):
            log.warning("question: FAIL-CLOSED — số không truy được nguồn (warnings=%s)",
                        list(result.warnings))
            message = ""
    if not message:
        message = _QUESTION_FALLBACK
    # Answering a customer's question is an interruption, not a clarification turn.
    # Resume the pending question without advancing clarify_count.
    next_q = state.get("next_question")
    if next_q and strip_accents(next_q.lower()) not in strip_accents(message.lower()):
        message = f"{message.rstrip()}\n\n{next_q}"
    log.info("question_node: answered (grounded trên %d sản phẩm)", len(cards))
    history = state.get("history", []) + [{"role": "assistant", "content": message}]
    return {"response": message, "stage": "collecting", "question": next_q,
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history,
            "clarify_count": state.get("clarify_count", 0)}


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


def comparison_followup_node(state: AgentState, config) -> AgentState:
    """Tái dùng đúng các sản phẩm vừa hiển thị; không phân loại lại ngành hay hỏi lại nhu cầu."""
    rows = _context_comparison_rows(state.get("query", ""), state.get("last_products", []) or [])
    res = {
        "status": "context_comparison",
        "sql_query": None,
        "sql_params": [],
        "context_filter": state.get("query", ""),
        "total_matches_found": len(rows),
        "top_3_products": rows[:4],
        "all_top_k": rows[:4],
    }
    log.info("comparison_followup_node: selected=%s",
             [product_display_name(row) for row in rows])
    return {"retrieval": res, "last_products": rows[:4], "focused_sku": None,
            "slot_stage": None, "top_n": min(4, len(rows))}


def retrieval_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang tìm máy phù hợp trong catalog…")
    intent = state.get("intent", {})
    slots = state.get("slots") or []
    # Số sản phẩm khoanh vùng: nếu khách vừa trả lời "top mấy?" thì lấy theo đó.
    top_n = state.get("top_n", 3)
    if state.get("slot_stage") == "await_compare_confirm":
        verdict, n = parse_compare_reply(state.get("query", ""))
        if verdict == "yes":
            top_n = n
    top_n = max(2, min(4, top_n))
    db_path = _cfg(config, "db_path")
    # Các slot đã thu thập (chỉ giá trị suy TRỰC TIẾP từ khách) -> tín hiệu chấm điểm.
    filled_slots = [(s["name"], str(s["value"])) for s in slots
                    if s.get("status") == "filled" and s.get("value")
                    and s.get("basis") in ("stated", "interpreted")]
    hard_slots = [(s["name"], str(s["value"])) for s in slots
                  if s.get("status") == "filled" and s.get("value") and s.get("hard")
                  and s.get("basis") in ("stated", "interpreted")]

    res = None
    if (intent.get("declines_more_info") and intent.get("category")
            and not intent.get("budget_max") and not filled_slots and not intent.get("is_meta_inquiry")):
        # Khách nhờ chọn giúp, chưa có nhu cầu cụ thể -> 3 đại diện rẻ/trung/cao.
        res = price_spread_products(intent["category"], db_path=db_path)
    elif intent.get("category") and not intent.get("is_meta_inquiry"):
        # ĐƯỜNG CHÍNH: điều kiện khách nói là bắt buộc phải có bằng chứng trong đúng cột DB;
        # sở thích mềm mới chỉ dùng để chấm điểm. Không khớp thì trả rỗng, không gợi ý khiên cưỡng.
        prefs = list(intent.get("priority_features") or [])
        if intent.get("brand"):
            prefs.append(str(intent["brand"]))
        res = retrieve_scored(intent.get("category"), intent.get("budget_max"),
                              filled_slots, prefs, top_n=top_n, db_path=db_path,
                              hard_slots=hard_slots, brand=intent.get("brand"),
                              required_terms=intent.get("required_features"))
    if res is None:
        # Chưa rõ ngành / meta -> khuôn tìm cũ (giữ hành vi meta_inquiry, no-category).
        res = search_products(
            query=state.get("query", ""), category=intent.get("category"),
            max_price=intent.get("budget_max"), brand=intent.get("brand"),
            priority_features=intent.get("priority_features"), top_k=5,
            db_path=db_path, is_meta_inquiry=intent.get("is_meta_inquiry", False))
        res["top_3_products"] = res.get("top_3_products", [])[:top_n]
    log.info("retrieval_node: status=%s total=%s top_n=%d top=%s | sql=%s",
             res.get("status"), res.get("total_matches_found"), top_n,
             [product_display_name(r) for r in res.get("top_3_products", [])],
             res.get("sql_query"))
    # Xoá trạng thái chờ xác nhận sau khi đã đề xuất.
    return {"retrieval": res, "last_products": res.get("top_3_products", []),
            "focused_sku": None, "slot_stage": None, "top_n": top_n}


def advisor_node(state: AgentState, config) -> AgentState:
    _notify(config, "Em đang soạn lời tư vấn…")
    intent = state.get("intent", {})
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    status = res.get("status", "exact_match")
    cards = build_cards(rows, intent.get("priority_features", []))
    advisor_intent = dict(intent)
    advisor_intent["relaxed_features"] = res.get("relaxed_features") or []
    message, _streamed, warnings = generate_advisor(
        state.get("query", ""), advisor_intent, rows, status, _cfg(config, "llm"), cards,
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
        wf.add_node("confirm_compare_node", confirm_compare_node)
        wf.add_node("chitchat_node", chitchat_node)
        wf.add_node("meta_inquiry_node", meta_inquiry_node)
        wf.add_node("unsupported_node", unsupported_node)
        wf.add_node("question_node", question_node)
        wf.add_node("detail_node", detail_node)
        wf.add_node("comparison_followup_node", comparison_followup_node)
        wf.add_node("retrieval_node", retrieval_node)
        wf.add_node("advisor_node", advisor_node)
        wf.add_node("compare_node", compare_node)
        wf.add_node("verify_node", verify_node)
        wf.add_edge(START, "intent_node")
        wf.add_conditional_edges("intent_node", router_edge,
                                 {"clarify": "clarify_node", "confirm_compare": "confirm_compare_node",
                                  "detail": "detail_node", "chitchat": "chitchat_node",
                                  "compare_followup": "comparison_followup_node",
                                  "meta_inquiry": "meta_inquiry_node", "unsupported": "unsupported_node",
                                  "question": "question_node", "retrieve": "retrieval_node"})
        wf.add_edge("clarify_node", END)
        wf.add_edge("confirm_compare_node", END)
        wf.add_edge("question_node", END)
        wf.add_edge("chitchat_node", END)
        wf.add_edge("meta_inquiry_node", END)
        wf.add_edge("unsupported_node", END)
        wf.add_edge("detail_node", END)
        wf.add_edge("comparison_followup_node", "advisor_node")
        wf.add_edge("retrieval_node", "advisor_node")
        wf.add_edge("advisor_node", "compare_node")
        wf.add_edge("compare_node", "verify_node")
        wf.add_edge("verify_node", END)
        _COMPILED = wf.compile(checkpointer=MemorySaver())
    return _COMPILED
