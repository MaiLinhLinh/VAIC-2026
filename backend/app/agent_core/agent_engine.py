from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agent_core.intent import extract_intent, has_enough_slots, kw_declines
from app.agent_core.retriever import (search_products, price_spread_products, get_catalog_metadata,
                                       category_table_for, hydrate_rows)
from app.agent_core.sql_tool import agent_query
from app.agent_core.advisor import build_cards, generate_advisor
from app.agent_core.compare import build_comparison
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)
from app.agent_core.slots import (spec_slot_columns, update_slots, reached_threshold,
                                   count_filled, count_touched, slots_summary)
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
    next_question: Optional[str]     # câu hỏi slot/chân dung do AI soạn cho lượt tới
    slot_stage: Optional[str]        # None | "await_compare_confirm"
    offered_touched: int             # mức 'touched' lúc gần nhất mời so sánh (chống mời lặp)
    top_n: int                       # số sản phẩm khách muốn khoanh để so sánh


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
    new_cat = intent.get("category")
    last_cat = state.get("last_category")
    out: Dict[str, Any] = {"intent": intent,
                           "history": history + [{"role": "user", "content": query}]}
    # Đổi NGÀNH HÀNG = nhu cầu mới -> reset toàn bộ trạng thái thu thập slot cho ngành mới.
    switched = bool(new_cat and last_cat and new_cat != last_cat)
    slots = [] if switched else list(state.get("slots") or [])
    if switched:
        out.update({"slot_stage": None, "offered_touched": -1, "top_n": 3})
    if new_cat:
        out["last_category"] = new_cat

    # Cập nhật slot ngành khi đã rõ ngành và lượt này KHÔNG phải xã giao/hỏi ngược/hỏi mặt hàng lạ.
    # Chạy cả khi đang chờ xác nhận so sánh: khách có thể trả lời "top mấy?" bằng cách BỔ SUNG
    # tiêu chí thay vì đồng ý -> vẫn cần bắt slot mới đó.
    side = intent.get("is_chitchat") or intent.get("is_meta_inquiry") or intent.get("unsupported_product")
    if new_cat and not side:
        db_path = _cfg(config, "db_path")
        cat_table = category_table_for(new_cat, db_path)
        cols = spec_slot_columns(cat_table, db_path) if cat_table else []
        res = update_slots(_cfg(config, "llm"), query, history, new_cat, cols, slots)
        out["slots"] = res["slots"]
        out["next_question"] = res["next_question"]
    else:
        out["slots"] = slots

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


def ends_with_question(query: str) -> bool:
    raw = (query or "").strip().rstrip(".!…~ ")
    if raw.endswith("?"):
        return True
    flat = strip_accents(raw.lower()).rstrip("?!. ")
    return any(flat.endswith(t) for t in _QUESTION_TAILS)


def asks_for_recommendation(query: str) -> bool:
    flat = strip_accents((query or "").lower())
    return any(k in flat for k in _RECO_REQUEST_KW)


def router_edge(state: AgentState) -> str:
    intent = state.get("intent", {})
    query = state.get("query", "")
    slots = state.get("slots") or []
    declines = bool(intent.get("declines_more_info"))
    wants_reco = asks_for_recommendation(query) or intent.get("wants_comparison")
    if _is_detail_followup(state):
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
    elif reached_threshold(slots):
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
            and ends_with_question(query) and not asks_for_recommendation(query)):
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
            "slot_stage": "await_compare_confirm", "offered_touched": count_touched(slots)}


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
            "clarify_count": state.get("clarify_count", 0) + 1}


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
    "- Kết thúc bằng MỘT câu mời khách hỏi tiếp hoặc cho biết thêm nhu cầu, không ép mua."
)

_QUESTION_FALLBACK = ("Dạ câu này em chưa có đủ dữ liệu để trả lời chính xác ạ. "
                      "Anh/chị cứ hỏi thêm hoặc cho em biết nhu cầu để em hỗ trợ tiếp nhé!")


def question_node(state: AgentState, config) -> AgentState:
    """Khách kết thúc lượt bằng câu hỏi: trả lời câu hỏi (grounded trên các máy đang tư vấn
    nếu có), không đưa sản phẩm mới, không bảng so sánh."""
    _notify(config, "Em đang trả lời câu hỏi của anh/chị…")
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    cards = [build_detail_card(r) for r in last[:3]]
    facts = facts_for_llm(cards) if cards else "(chưa có sản phẩm nào trong ngữ cảnh)"
    user = (f"FACTS:\n{facts}\n\nCâu hỏi của khách: \"{query}\"\n\n"
            "Trả lời khách theo đúng quy tắc.")
    llm = _cfg(config, "llm")
    message = ""
    if llm is not None:
        try:
            message = llm.complete_text(_QUESTION_SYSTEM, user)
        except Exception as e:
            log.warning("question: LLM lỗi (%s)", e)
    if message:
        result = verify_advice(AdviceResult(message=message, cards=cards, assumptions=[], warnings=[]))
        if not is_grounded(result):
            log.warning("question: FAIL-CLOSED — số không truy được nguồn (warnings=%s)",
                        list(result.warnings))
            message = ""
    if not message:
        message = _QUESTION_FALLBACK
    log.info("question_node: answered (grounded trên %d sản phẩm)", len(cards))
    history = state.get("history", []) + [{"role": "assistant", "content": message}]
    return {"response": message, "stage": "collecting", "question": None,
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
    slots = state.get("slots") or []
    # Số sản phẩm khoanh vùng: nếu khách vừa trả lời "top mấy?" thì lấy theo đó.
    top_n = state.get("top_n", 3)
    if state.get("slot_stage") == "await_compare_confirm":
        verdict, n = parse_compare_reply(state.get("query", ""))
        if verdict == "yes":
            top_n = n
    top_n = max(2, min(4, top_n))
    # Slot đã thu thập -> đưa vào truy vấn để lọc/xếp đúng nhu cầu ngành.
    slot_txt = slots_summary(slots)
    q_for_sql = state.get("query", "")
    if slot_txt:
        q_for_sql += f"\n[Nhu cầu đã khoanh vùng] {slot_txt}"

    res = None
    if (intent.get("declines_more_info") and intent.get("category")
            and not intent.get("budget_max") and not slot_txt and not intent.get("is_meta_inquiry")):
        # Khách nhờ chọn giúp, chưa có nhu cầu cụ thể -> 3 đại diện rẻ/trung/cao thay vì top điểm.
        res = price_spread_products(intent["category"], db_path=_cfg(config, "db_path"))
    elif not intent.get("is_meta_inquiry") and _cfg(config, "llm") is not None:
        # Đường chính cho MỌI truy vấn tìm hàng: tool SQL — AI soạn SELECT theo schema md +
        # slot đã khoanh vùng, tự sửa tối đa 3 lần; thất bại -> khuôn cũ.
        db_path = _cfg(config, "db_path")
        cat_table = category_table_for(intent["category"], db_path) if intent.get("category") else None
        agent_res = agent_query(_cfg(config, "llm"), q_for_sql, intent, cat_table, db_path)
        if agent_res is not None and agent_res.get("rows"):
            prods = hydrate_rows(agent_res["rows"], db_path)
            if prods:
                res = {"status": "custom_query", "sql_query": agent_res.get("sql", ""),
                       "total_matches_found": len(prods),
                       "top_3_products": prods[:top_n], "all_top_k": prods[:5]}
        if res is None:
            # SQL chặt quá -> 0 dòng: NỚI LỎNG về khuôn tìm mềm (category + ngân sách + xếp theo
            # slot/ưu tiên) thay vì báo "không có". Chỉ thực sự no_products khi khuôn này cũng rỗng.
            log.info("retrieval_node: tool SQL 0 dòng -> nới lỏng bằng khuôn tìm mềm")
    if res is None:
        # Đưa các slot đã thu thập vào ưu tiên để khuôn mềm xếp hạng theo đúng nhu cầu.
        prefs = list(intent.get("priority_features") or [])
        prefs += [str(s.get("value")) for s in slots
                  if s.get("status") == "filled" and s.get("value")]
        res = search_products(
            query=state.get("query", ""),
            category=intent.get("category"),
            max_price=intent.get("budget_max"),
            brand=intent.get("brand"),
            priority_features=prefs,
            top_k=5,
            db_path=_cfg(config, "db_path"),
            is_meta_inquiry=intent.get("is_meta_inquiry", False),
        )
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
        wf.add_node("confirm_compare_node", confirm_compare_node)
        wf.add_node("chitchat_node", chitchat_node)
        wf.add_node("meta_inquiry_node", meta_inquiry_node)
        wf.add_node("unsupported_node", unsupported_node)
        wf.add_node("question_node", question_node)
        wf.add_node("detail_node", detail_node)
        wf.add_node("retrieval_node", retrieval_node)
        wf.add_node("advisor_node", advisor_node)
        wf.add_node("compare_node", compare_node)
        wf.add_node("verify_node", verify_node)
        wf.add_edge(START, "intent_node")
        wf.add_conditional_edges("intent_node", router_edge,
                                 {"clarify": "clarify_node", "confirm_compare": "confirm_compare_node",
                                  "detail": "detail_node", "chitchat": "chitchat_node",
                                  "meta_inquiry": "meta_inquiry_node", "unsupported": "unsupported_node",
                                  "question": "question_node", "retrieve": "retrieval_node"})
        wf.add_edge("clarify_node", END)
        wf.add_edge("confirm_compare_node", END)
        wf.add_edge("question_node", END)
        wf.add_edge("chitchat_node", END)
        wf.add_edge("meta_inquiry_node", END)
        wf.add_edge("unsupported_node", END)
        wf.add_edge("detail_node", END)
        wf.add_edge("retrieval_node", "advisor_node")
        wf.add_edge("advisor_node", "compare_node")
        wf.add_edge("compare_node", "verify_node")
        wf.add_edge("verify_node", END)
        _COMPILED = wf.compile(checkpointer=MemorySaver())
    return _COMPILED
