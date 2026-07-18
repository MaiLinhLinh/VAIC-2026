import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from app.agent_core.retriever import get_catalog_metadata, get_schema_summary


# Pydantic schema mô tả ý định tìm kiếm sản phẩm.
class IntentSchema(BaseModel):
    is_meta_inquiry: bool = Field(
        default=False,
        description="True nếu người dùng hỏi tổng quan về hệ thống/danh mục thay vì tìm mua sản phẩm cụ thể."
    )
    category: Optional[str] = Field(
        default=None,
        description="Tên danh mục sản phẩm trong CSDL phù hợp nhất, hoặc None nếu không xác định."
    )
    budget_max: Optional[float] = Field(
        default=None,
        description="Ngân sách tối đa tính bằng VNĐ (15 triệu -> 15000000.0). None nếu chưa nhắc."
    )
    brand: Optional[str] = Field(
        default=None,
        description="Thương hiệu người dùng quan tâm. None nếu không nhắc đến."
    )
    priority_features: List[str] = Field(
        default_factory=list,
        description="Danh sách tính năng/tiêu chí/mục đích sử dụng thực tế của người dùng."
    )
    needs_clarification: bool = Field(
        default=False,
        description="True nếu câu hỏi quá chung chung, chưa đủ dữ kiện để tư vấn chính xác."
    )
    clarification_questions: List[str] = Field(
        default_factory=list,
        description="1-2 câu hỏi làm rõ lịch sự nếu needs_clarification là True."
    )


def extract_intent_fallback(query: str, history: Optional[List[Dict[str, str]]] = None,
                            db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Dynamic semantic fallback extractor using database metadata when LLM API is unavailable.
    Zero hardcoded mapping dictionaries.
    """
    meta = get_catalog_metadata(db_path)
    categories = meta["categories"]
    brands = meta["brands"]
    query_lower = query.lower()

    # Dynamic category matching (sort by length descending to match longer specific names first)
    matched_category = None
    sorted_categories = sorted(categories, key=lambda x: len(x), reverse=True)
    for cat in sorted_categories:
        cat_lower = cat.lower()
        if cat_lower in query_lower:
            matched_category = cat
            break

    if not matched_category:
        for cat in sorted_categories:
            cat_lower = cat.lower()
            if "máy tính để bàn" in cat_lower and any(w in query_lower for w in ["laptop", "pc", "macbook", "desktop"]):
                matched_category = cat
                break
            if "máy tính bảng" in cat_lower and any(w in query_lower for w in ["tablet", "ipad"]):
                matched_category = cat
                break
            if "tủ mát" in cat_lower and any(w in query_lower for w in ["tủ đông", "freezer"]):
                matched_category = cat
                break

    # Inherit category from previous user turn only (never from AI assistant)
    if not matched_category and history:
        for msg in reversed(history):
            if msg.get("role") == "user":
                prev_text = msg.get("content", "").lower()
                for cat in categories:
                    if cat.lower() in prev_text or ("laptop" in prev_text and "Máy tính để bàn" in cat):
                        matched_category = cat
                        break
                if matched_category:
                    break

    # Extract budget dynamically
    budget_max = None
    m_trieu = re.search(r'(\d+(?:\.\d+)?)\s*(?:triệu|tr|củ|trd)', query_lower)
    if m_trieu:
        try:
            budget_max = float(m_trieu.group(1)) * 1000000
        except Exception:
            pass
    else:
        m_nghin = re.search(r'(\d{4,8})\s*(?:k|nghìn|ngàn)', query_lower)
        if m_nghin:
            try:
                budget_max = float(m_nghin.group(1)) * 1000
            except Exception:
                pass

    # Extract brand dynamically from database brands
    matched_brand = None
    for b in brands:
        if re.search(r'\b' + re.escape(b.lower()) + r'\b', query_lower):
            matched_brand = b
            break

    # Check for meta inquiry dynamically
    is_meta_inquiry = False
    if not matched_category and not budget_max and not matched_brand and any(w in query_lower for w in ["bao nhiêu", "danh mục", "loại nào", "những dòng", "sản phẩm nào", "hiện có", "những gì"]):
        is_meta_inquiry = True

    # Extract priority features dynamically without hardcoded keyword lists
    stop_words = {
        "tôi", "cần", "mua", "tìm", "cho", "chiếc", "cái", "dòng", "loại", "máy", "tính", "bàn", "là", "và",
        "nhu", "cầu", "mục", "đích", "chính", "bao", "nhiêu", "tiền", "triệu", "tr", "k", "nghìn", "ngàn",
        "của", "tại", "với", "có", "không", "nhưng", "để", "làm", "phục", "vụ", "dùng", "thì", "đang", "quan", "tâm"
    }
    clean_query = query_lower
    if matched_category:
        for word in matched_category.lower().split():
            clean_query = clean_query.replace(word, " ")
    if matched_brand:
        clean_query = clean_query.replace(matched_brand.lower(), " ")

    priority_features = []
    for token in clean_query.split():
        clean_token = re.sub(r'[^\w]', '', token)
        if len(clean_token) > 2 and clean_token not in stop_words and not clean_token.isdigit():
            priority_features.append(clean_token)

    # Check clarification need
    needs_clarification = False
    clarification_questions = []

    replying_to_clarify = False
    if history and len(history) >= 1:
        last_msg = history[-1]
        if last_msg.get("role") == "assistant" and "?" in last_msg.get("content", ""):
            replying_to_clarify = True

    if is_meta_inquiry:
        needs_clarification = False
    elif not matched_category and len(query.split()) < 6 and not replying_to_clarify and not priority_features:
        needs_clarification = True
        clarification_questions = [
            f"Bạn đang quan tâm đến dòng sản phẩm nào trong các danh mục hiện có ({', '.join(categories[:5])}...)?",
            "Mức ngân sách dự kiến của bạn khoảng bao nhiêu để tôi hỗ trợ sàng lọc?"
        ]
    elif matched_category and not budget_max and not priority_features and len(query.split()) < 7 and not replying_to_clarify:
        needs_clarification = True
        clarification_questions = [
            f"Bạn tìm mua {matched_category} phục vụ cho nhu cầu hoặc mục đích sử dụng chính là gì?",
            "Ngân sách tối đa bạn dự kiến đầu tư cho sản phẩm này là bao nhiêu?"
        ]

    return {
        "is_meta_inquiry": is_meta_inquiry,
        "category": matched_category,
        "budget_max": budget_max,
        "brand": matched_brand,
        "priority_features": priority_features,
        "needs_clarification": needs_clarification,
        "clarification_questions": clarification_questions
    }


_SCHEMA_HINT = (
    '{"is_meta_inquiry": bool, "category": string|null, "budget_max": number|null, '
    '"brand": string|null, "priority_features": string[], "needs_clarification": bool, '
    '"clarification_questions": string[]}'
)


def extract_intent(query: str, history: Optional[List[Dict[str, str]]] = None,
                   llm=None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Trích ý định qua DeepSeek (LLMClient.complete_json). Lỗi/không có llm -> fallback heuristic."""
    if llm is None:
        return extract_intent_fallback(query, history, db_path)
    try:
        schema_info = get_schema_summary(db_path)
        system = (
            "Bạn là AI phân tích ý định tìm mua điện máy. "
            f"{schema_info}\n"
            "Ánh xạ danh mục theo ngữ nghĩa (laptop/macbook/pc/desktop -> 'Máy tính để bàn'; "
            "ipad/tablet -> 'Máy tính bảng'). Nếu câu hỏi mới đổi loại sản phẩm so với lịch sử, "
            "BẮT BUỘC theo danh mục mới. Nếu Assistant vừa hỏi làm rõ và người dùng đã trả lời mục đích "
            "hoặc ngân sách, đặt needs_clarification=false và đưa mục đích vào priority_features. "
            "Nếu thiếu ngân sách/nhu cầu và chưa từng hỏi, đặt needs_clarification=true kèm 1-2 câu hỏi."
        )
        hist_str = ""
        for m in (history or []):
            role = "User" if m.get("role") == "user" else "Assistant"
            hist_str += f"{role}: {m.get('content')}\n"
        user = f"Lịch sử:\n{hist_str or 'Không có'}\n\nCâu hỏi mới: {query}"
        raw = llm.complete_json(system, user, _SCHEMA_HINT)
        return IntentSchema(**{k: raw[k] for k in IntentSchema.model_fields if k in raw}).model_dump()
    except Exception as e:
        print(f"[Intent LLM Error]: {e}")
        return extract_intent_fallback(query, history, db_path)


def has_enough_slots(intent: Dict[str, Any]) -> bool:
    """Thông tin tối thiểu để tiến hành tìm kiếm mà không cần hỏi thêm."""
    cat = intent.get("category")
    budget = intent.get("budget_max")
    brand = intent.get("brand")
    feats = intent.get("priority_features", [])
    if not cat and not budget and not brand and not feats:
        return False
    if cat and (budget or brand or (feats and len(feats) > 0)):
        return True
    if intent.get("needs_clarification") and not (budget or (feats and len(feats) > 0)):
        return False
    return True
