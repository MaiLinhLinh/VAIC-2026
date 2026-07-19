import logging
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from app.agent_core.retriever import get_catalog_metadata, get_schema_summary
from app.nlu.preprocess import strip_accents

log = logging.getLogger("agent_core")

# Lưới dự phòng nhận diện khách từ chối (khi LLM chết); đường chính là ô
# declines_more_info do LLM điền — hiểu được cả cách nói không có trong list.
_DECLINE_KW = ["goi y dai", "cu goi y", "goi y luon", "gi cung duoc", "sao cung duoc",
               "tuy em", "tuy ban", "tu van dai",
               "chon giup", "chon dai", "khoi hoi"]
_UNKNOWN_KW = ("khong biet", "chua biet", "khong ro", "chua ro", "ko biet", "ko ro")


def kw_declines(query: str) -> bool:
    flat = strip_accents(query.lower())
    return any(k in flat for k in _DECLINE_KW)


def kw_unknown_answer(query: str) -> bool:
    flat = strip_accents(query.lower())
    return any(k in flat for k in _UNKNOWN_KW)


# Pydantic schema mô tả ý định tìm kiếm sản phẩm.
class IntentSchema(BaseModel):
    is_meta_inquiry: bool = Field(
        default=False,
        description="True NẾU VÀ CHỈ NẾU khách hỏi giải thích khái niệm, thông số kỹ thuật (VD 'OLED là gì?'), thắc mắc về tiêu chí (VD 'dung tích là sao?'), HOẶC hỏi về các loại/dòng sản phẩm nói chung (VD 'tủ lạnh có những loại nào?', 'các loại máy tính')."
    )
    meta_reply: Optional[str] = Field(
        default=None,
        description="Nếu is_meta_inquiry=true: 1-2 câu giải thích ngắn gọn, dân dã về khái niệm/thông số đó (kèm lợi ích thực tế nếu có), sau đó BẮT BUỘC đặt lại câu hỏi khéo léo để tiếp tục lấy thông tin (VD 'Dạ Inverter giúp tiết kiệm điện ạ. Nhà mình định mua máy tầm giá bao nhiêu?')."
    )
    is_chitchat: bool = Field(
        default=False,
        description="True nếu tin nhắn chỉ là xã giao/ngoài chủ đề mua sắm (chào hỏi, đùa vui, hỏi vu vơ về shop/bot) và KHÔNG chứa nhu cầu sản phẩm nào."
    )
    smalltalk_reply: Optional[str] = Field(
        default=None,
        description="Khi is_chitchat=true: 1-2 câu đáp thân thiện tự nhiên theo đúng câu khách nói, kết thúc bằng lời mời khách cho biết nhu cầu mua sắm. Không chứa số liệu."
    )
    category: Optional[str] = Field(
        default=None,
        description="Tên danh mục sản phẩm trong CSDL phù hợp nhất, hoặc None nếu không xác định."
    )
    transition_message: Optional[str] = Field(
        default=None,
        description="Lời chuyển tiếp tự nhiên, giải thích khéo léo lý do chọn danh mục này khi khách chỉ nêu vấn đề chứ không gọi tên sản phẩm (VD: 'Dạ nếu cô giáo không cho mang điện thoại thì bé nhà mình mang đồng hồ thông minh có nghe gọi được không ạ?')."
    )
    unsupported_product: Optional[str] = Field(
        default=None,
        description="Loại sản phẩm khách muốn mua nhưng KHÔNG thuộc danh mục nào trong CSDL (VD 'điện thoại'). None nếu khách hỏi đúng mặt hàng có bán."
    )
    related_categories: List[str] = Field(
        default_factory=list,
        description="Khi unsupported_product khác None: 1-3 danh mục CÓ TRONG CSDL gần nhất với nhu cầu đó (VD điện thoại -> Máy tính bảng, Đồng hồ thông minh)."
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
        description="Các tính năng hoặc thông số đặc thù người dùng ưu tiên (màn hình lớn, pin trâu, mỏng nhẹ...)."
    )
    required_features: List[str] = Field(
        default_factory=list,
        description="Loại/công nghệ/tính năng khách nói rõ là bắt buộc hoặc gọi đích danh khi tìm (máy in laser -> laser; phải có Wi-Fi -> Wi-Fi)."
    )
    wants_comparison: bool = Field(
        default=False,
        description="True NẾU VÀ CHỈ NẾU khách CHỦ ĐỘNG yêu cầu xem nhiều lựa chọn, so sánh, phân tích các mẫu khác nhau (VD: 'so sánh', 'có mấy loại', 'xem các option', 'mẫu nào tốt nhất'). False nếu khách chỉ đưa ra một nhu cầu chung chung và đang cần tư vấn 1 sản phẩm phù hợp nhất."
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Các suy đoán bạn tự rút ra mà khách KHÔNG nói rõ (VD 'mua cho con' -> 'bé dùng để học tập'). Để [] nếu không suy đoán gì."
    )
    declines_more_info: bool = Field(
        default=False,
        description="True nếu khách né tránh/từ chối cung cấp thêm thông tin ('gợi ý đại đi', 'gì cũng được', 'em cứ chọn giúp anh')."
    )
    needs_custom_query: bool = Field(
        default=False,
        description="True nếu nhu cầu có ràng buộc THÔNG SỐ hoặc cách xếp hạng mà lọc cơ bản (ngành/giá trần/hãng) không làm được: VD 'trên 300 lít', 'màn 27 inch', 'tủ 2 cửa', 'ít tốn điện nhất', 'nhẹ nhất'."
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

    # Xã giao/ngoài chủ đề (fallback thô: câu ngắn mở đầu bằng lời chào, không chứa nhu cầu)
    flat_q = strip_accents(query_lower).strip()
    is_chitchat = (not matched_category and not budget_max and not matched_brand
                   and not is_meta_inquiry and len(query.split()) <= 6
                   and any(flat_q.startswith(k) for k in ("hi", "hello", "helo", "alo", "chao", "xin chao", "hey", "shop oi", "em oi")))

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

    log.info("intent(fallback): replying_to_clarify=%s (lượt trước là câu hỏi của trợ lý -> "
             "không hỏi tiếp dù còn thiếu slot)", replying_to_clarify)
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
        "is_chitchat": is_chitchat,
        "smalltalk_reply": None,
        "category": matched_category,
        "transition_message": None,
        "unsupported_product": None,
        "related_categories": [],
        "budget_max": budget_max,
        "brand": matched_brand,
        "priority_features": priority_features,
        "required_features": [],
        "wants_comparison": False,
        "assumptions": [],
        "declines_more_info": kw_declines(query),
        "needs_custom_query": False,
        "needs_clarification": needs_clarification,
        "clarification_questions": clarification_questions
    }


_SCHEMA_HINT = (
    '{"is_meta_inquiry": bool, "meta_reply": string|null, "is_chitchat": bool, "smalltalk_reply": string|null, '
    '"category": string|null, "transition_message": string|null, "unsupported_product": string|null, '
    '"related_categories": string[], "budget_max": number|null, '
    '"brand": string|null, "priority_features": string[], "required_features": string[], '
    '"wants_comparison": bool, "assumptions": string[], '
    '"declines_more_info": bool, "needs_custom_query": bool, "needs_clarification": bool, '
    '"clarification_questions": string[]}'
)


def extract_intent(query: str, history: Optional[List[Dict[str, str]]] = None,
                   llm=None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Trích ý định qua DeepSeek (LLMClient.complete_json). Lỗi/không có llm -> fallback heuristic."""
    if llm is None:
        log.info("intent: không có LLM -> dùng fallback heuristic")
        return extract_intent_fallback(query, history, db_path)
    try:
        schema_info = get_schema_summary(db_path)
        system = (
            "Bạn là nhân viên tư vấn điện máy đang lắng nghe khách. "
            f"{schema_info}\n"
            "Ánh xạ danh mục theo ngữ nghĩa (VD: laptop/macbook/pc/desktop -> 'Máy tính để bàn'; "
            "ipad/tablet -> 'Máy tính bảng', ...). Nếu câu hỏi mới đổi loại sản phẩm so với lịch sử, "
            "BẮT BUỘC theo danh mục mới.\n"
            "- Nếu khách mô tả một BÀI TOÁN/VẤN ĐỀ thay vì gọi tên sản phẩm (VD: 'bé đi học không được dùng điện thoại nhưng cần liên lạc', 'mùa mưa phơi đồ không khô'), HÃY TỰ SUY LUẬN xem trong các danh mục có sẵn có loại nào giải quyết được không (VD: Đồng hồ thông minh có nghe gọi, Máy sấy quần áo). Nếu có, gán luôn `category` là danh mục đó và BẮT BUỘC viết `transition_message` để giải thích gợi mở khéo léo (VD: 'Dạ nếu cô giáo không cho mang điện thoại thì bé dùng đồng hồ thông minh có nghe gọi được không ạ?').\n"
            "- Nếu khách muốn mua loại sản phẩm KHÔNG thuộc danh mục nào trong CSDL (VD điện thoại, "
            "tivi, nồi cơm điện) và cũng KHÔNG thể dùng sản phẩm nào trong CSDL để thay thế: TUYỆT ĐỐI không gán bừa category gần đúng — để category=null, điền "
            "unsupported_product=<tên loại đó>, và chọn related_categories là 1-3 danh mục CÓ THẬT "
            "trong CSDL gần với nhu cầu đó nhất.\n"
            "- is_meta_inquiry=true KHI VÀ CHỈ KHI khách hỏi yêu cầu giải thích về một khái niệm, thông số kỹ thuật (VD: 'màn OLED là gì?', 'dung tích là gì?'), thắc mắc tiêu chí, HOẶC hỏi về các phân loại sản phẩm (VD 'tủ lạnh có những dòng nào?'). Lúc này BẮT BUỘC điền meta_reply giải đáp ngắn gọn, nêu các phân loại phổ biến rồi hỏi lại để tiếp tục tư vấn. NẾU is_meta_inquiry=true, BẮT BUỘC để category=null và priority_features=[] để tránh hệ thống tự động tìm kiếm sản phẩm.\n"
            "- wants_comparison=true khi khách chủ động yêu cầu đưa ra nhiều sự lựa chọn hoặc so sánh (VD 'so sánh', 'có những option nào', 'các dòng máy'). False nếu khách chỉ nhờ tư vấn chung.\n"
            "- required_features chỉ chứa điều kiện sản phẩm phải khớp: loại/công nghệ khách gọi đích danh "
            "hoặc nói là bắt buộc (VD 'máy in laser' -> ['laser'], 'phải có Wi-Fi' -> ['Wi-Fi']). "
            "Sở thích mềm để trong priority_features, không đưa vào required_features.\n"
            "- needs_clarification=true khi KHÁCH TRẢ LỜI QUÁ CHUNG CHUNG và bạn cần hỏi thêm để lọc sản phẩm (mục đích, bối cảnh người dùng, ngân sách). TUYỆT ĐỐI KHÔNG bật cờ này nếu khách đang hỏi ngược lại bạn (đó là is_meta_inquiry). False nếu khách vừa trả lời đủ hoặc từ chối bổ sung.\n"
            "- clarification_questions: 1-2 câu hỏi NGẮN, tự nhiên như người bán hàng thật, bám đúng bối cảnh "
            "khách vừa kể. TUYỆT ĐỐI không hỏi lại điều khách đã nói hoặc điều trợ lý đã hỏi trong lịch sử.\n"
            "- assumptions: các suy đoán bạn tự rút ra mà khách không nói rõ, ghi ngắn gọn.\n"
            "- declines_more_info=true nếu khách từ chối TOÀN BỘ việc hỏi thêm ('gợi ý đại', 'gì cũng được', "
            "'chọn giúp anh/chị',...). Nếu khách chỉ nói 'không biết/chưa rõ' để trả lời CÂU HIỆN TẠI thì "
            "declines_more_info=false: hệ thống sẽ ghi nhận lượt đó và tiếp tục sang câu kế tiếp.\n"
            "- needs_custom_query=true khi khách ràng buộc theo THÔNG SỐ hoặc cách xếp hạng đặc biệt "
            "(dung tích/kích thước/số cửa/'ít tốn điện nhất'/'nhẹ nhất'...) — lọc cơ bản ngành+giá+hãng "
            "không đáp ứng được.\n"
            "- is_chitchat=true khi tin nhắn CHỈ là xã giao/ngoài chủ đề (VD 'hi em', 'shop có muốn chat "
            "không', 'bạn là ai', câu đùa) — khi đó soạn smalltalk_reply: đáp thân thiện đúng ý câu khách "
            "rồi mời khách nêu nhu cầu mua sắm; KHÔNG nhầm với is_meta_inquiry (khách hỏi shop bán những "
            "gì). Nếu câu vừa chào vừa nêu nhu cầu ('chào em, cần mua tủ lạnh') thì KHÔNG phải chitchat.\n"
            "- is_meta_inquiry=true CHỈ KHI khách hỏi TỔNG QUAN về toàn bộ cửa hàng (VD: 'shop bán những gì', "
            "'có các danh mục nào'). Nếu khách hỏi về MỘT nhóm sản phẩm cụ thể (VD: 'có máy tính không', "
            "'bán tủ lạnh không'), tuyệt đối KHÔNG gán meta_inquiry, hãy gán trực tiếp category tương ứng."
        )
        hist_str = ""
        for m in (history or []):
            role = "User" if m.get("role") == "user" else "Assistant"
            hist_str += f"{role}: {m.get('content')}\n"
        user = f"Lịch sử:\n{hist_str or 'Không có'}\n\nCâu hỏi mới: {query}"
        raw = llm.complete_json(system, user, _SCHEMA_HINT)
        log.info("intent: trích qua LLM thành công")
        return IntentSchema(**{k: raw[k] for k in IntentSchema.model_fields if k in raw}).model_dump()
    except Exception as e:
        log.warning("intent: LLM lỗi (%s) -> dùng fallback heuristic", e)
        return extract_intent_fallback(query, history, db_path)


def has_enough_slots(intent: Dict[str, Any]) -> bool:
    """Thông tin tối thiểu để tiến hành tìm kiếm mà không cần hỏi thêm."""
    if intent.get("declines_more_info"):
        return True

    cat = intent.get("category")
    budget = intent.get("budget_max")
    brand = intent.get("brand")
    feats = intent.get("priority_features", [])
    
    has_filter = bool(budget or brand or (feats and len(feats) > 0))
    
    if not cat and not has_filter:
        return False
        
    # KHI VÀ CHỈ KHI có cat và có ít nhất 1 filter (giá, hãng, tính năng), thì mới đủ điều kiện.
    if cat and not has_filter:
        return False
        
    if intent.get("needs_clarification") and not has_filter:
        return False
        
    return True
