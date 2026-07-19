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
               "tuy em", "tuy ban", "khong biet", "chua biet", "tu van dai",
               "chon giup", "chon dai", "khoi hoi"]


def kw_declines(query: str) -> bool:
    flat = strip_accents(query.lower())
    return any(k in flat for k in _DECLINE_KW)


# Lưới dự phòng nhận diện câu hỏi chính sách/vận hành cửa hàng (khi LLM chết hoặc bỏ sót).
_POLICY_KW = ["gio mo cua", "gio dong cua", "may gio mo", "may gio dong", "mo cua luc",
              "gio hoat dong", "tong dai", "hotline", "so dien thoai cua shop",
              "khieu nai", "hoan tien", "doi tra", "tra hang", "cach dat hang",
              "dat hang online", "mua online", "thanh toan", "tra gop", "cod",
              "phi giao hang", "phi van chuyen", "chinh sach", "noi quy", "quy che",
              "dia chi cua hang", "dia chi shop", "cua hang o dau", "shop o dau",
              "du lieu ca nhan", "thong tin ca nhan", "bao mat thong tin", "quyen rieng tu",
              "xoa du lieu", "xoa thong tin", "xoa tai khoan", "thu thap du lieu",
              "thu thap thong tin", "chia se du lieu", "chia se thong tin", "lo thong tin",
              "rut lai su dong y", "luu thong tin", "luu du lieu",
              "phi lap dat", "lap dat mien phi", "cong lap dat", "phi ship", "mien phi ship",
              "giao bao lau", "giao trong bao lau", "bao lau nhan hang", "may gio giao",
              "thoi gian giao", "hu gi doi nay", "1 doi 1", "mot doi mot", "phi doi",
              "phi tra hang", "ve sinh may lanh", "thay pin", "thay loi loc"]


def kw_policy(query: str) -> bool:
    flat = strip_accents(query.lower())
    # So khớp cả cụm theo ranh giới từ. Dùng substring khiến keyword "cod"
    # khớp nhầm chữ "code" và đẩy yêu cầu lập trình vào policy_node.
    return any(
        re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", flat) is not None
        for keyword in _POLICY_KW
    )


# Một số mặt hàng phổ biến không nằm trong catalog nhưng thường xuất hiện trong
# hội thoại hoặc tài liệu chính sách nguồn. Danh sách này chỉ là lưới an toàn khi
# LLM intent không điền unsupported_product; category hợp lệ do catalog cung cấp
# vẫn luôn được ưu tiên.
_KNOWN_UNSUPPORTED_PRODUCTS = (
    ("tivi", ("tivi", "ti vi", "tv")),
    ("điện thoại", ("dien thoai", "smartphone", "smart phone")),
    ("nồi cơm điện", ("noi com dien",)),
    ("dàn âm thanh", ("dan am thanh",)),
    ("bếp gas", ("bep gas",)),
    ("bếp điện", ("bep dien",)),
    ("camera", ("camera",)),
    ("quạt trần", ("quat tran",)),
    ("máy lọc nước", ("may loc nuoc",)),
    ("xe đạp", ("xe dap",)),
    ("máy bơm nước", ("may bom nuoc",)),
    ("ổn áp", ("on ap",)),
    ("bộ lưu điện", ("bo luu dien",)),
    ("bồn nước", ("bon nuoc",)),
)

_PHONE_NON_PRODUCT_CONTEXTS = (
    "so dien thoai", "goi dien thoai", "qua dien thoai", "bang dien thoai",
    "lien he dien thoai", "dien thoai lien he", "dien thoai cua shop",
    "dien thoai shop", "dien thoai cua cua hang", "hotline",
)

_PROGRAMMING_REQUEST_KW = (
    "code cho", "code file", "code c++", "viet code", "viet chuong trinh",
    "tao file c++", "sua code", "debug code", "hello world",
)
_OTHER_OFF_TOPIC_KW = (
    "viet tho", "ke chuyen", "dich van ban", "giai bai tap",
)
_SHOPPING_SIGNALS = (
    "muon mua", "can mua", "tim mua", "tu van", "gia bao nhieu", "san pham nao",
    "loai nao", "may nao",
)


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def is_programming_request(query: str) -> bool:
    flat = strip_accents(query.lower())
    return any(keyword in flat for keyword in _PROGRAMMING_REQUEST_KW)


def is_off_topic_request(query: str) -> bool:
    """Yêu cầu hành động/kiến thức, không phải một mặt hàng khách muốn mua."""
    flat = strip_accents(query.lower())
    if any(signal in flat for signal in _SHOPPING_SIGNALS):
        return False
    return is_programming_request(query) or any(k in flat for k in _OTHER_OFF_TOPIC_KW)


def detect_known_unsupported_product(query: str, categories: List[str]) -> Optional[str]:
    """Nhận diện dự phòng mặt hàng ngoài catalog, không nhầm số điện thoại/liên hệ."""
    flat = strip_accents(query.lower())

    # Nếu khách gọi đúng tên category thật (vd "Micro thu âm điện thoại") thì
    # tuyệt đối không cắt riêng chữ "điện thoại" thành hàng unsupported.
    if any(_has_phrase(flat, strip_accents(cat.lower())) for cat in categories):
        return None

    for canonical, aliases in _KNOWN_UNSUPPORTED_PRODUCTS:
        if canonical == "điện thoại" and any(ctx in flat for ctx in _PHONE_NON_PRODUCT_CONTEXTS):
            continue
        if any(_has_phrase(flat, alias) for alias in aliases):
            return canonical
    return None


def normalize_intent_scope(intent: Dict[str, Any], query: str,
                           categories: List[str]) -> Dict[str, Any]:
    """Ép category/unsupported về đúng catalog trước khi router ra quyết định."""
    out = dict(intent)
    by_lower = {cat.lower(): cat for cat in categories}
    category = str(out.get("category") or "").strip()
    unsupported = str(out.get("unsupported_product") or "").strip()
    detected = detect_known_unsupported_product(query, categories)

    # "Viết code C++" là yêu cầu ngoài chủ đề, không phải mặt hàng tên
    # "code C++". Nhánh hội thoại sẽ từ chối ngắn và dẫn về mua sắm.
    if is_off_topic_request(query):
        out["category"] = None
        out["unsupported_product"] = None
        out["related_categories"] = []
        out["is_policy_question"] = False
        out["is_meta_inquiry"] = False
        out["is_chitchat"] = True
        out["needs_clarification"] = False
        out["clarification_questions"] = []
        return out

    if unsupported:
        # Schema quy định hai trường loại trừ nhau; hàng unsupported phải thắng
        # một category gần đúng mà LLM có thể đồng thời điền nhầm.
        out["category"] = None
        out["unsupported_product"] = unsupported
    elif detected and (not category or not out.get("transition_message")):
        # Mặt hàng được gọi tên ở câu hiện tại phải thắng category kế thừa từ
        # lịch sử. Ngoại lệ duy nhất là LLM chủ động ánh xạ sang một giải pháp
        # catalog và có transition_message giải thích phép thay thế đó.
        out["category"] = None
        out["unsupported_product"] = detected
    elif category:
        canonical = by_lower.get(category.lower())
        if canonical:
            out["category"] = canonical
            out["unsupported_product"] = None
        else:
            # Category do LLM sinh nhưng không tồn tại trong DB cũng là unsupported.
            out["category"] = None
            out["unsupported_product"] = category
    else:
        out["category"] = None
        out["unsupported_product"] = detected

    valid = set(categories)
    out["related_categories"] = [
        cat for cat in (out.get("related_categories") or []) if cat in valid
    ][:3]
    if out.get("unsupported_product"):
        out["needs_clarification"] = False
        out["is_meta_inquiry"] = False
        out["is_chitchat"] = False
    return out


# Pydantic schema mô tả ý định tìm kiếm sản phẩm.
class IntentSchema(BaseModel):
    is_meta_inquiry: bool = Field(
        default=False,
        description="True khi khách hỏi khái niệm/thông số liên quan sản phẩm (OLED, Inverter, dung tích...) hoặc hỏi tổng quan catalog. Không dùng cho kiến thức phổ thông ngoài mua sắm."
    )
    meta_reply: Optional[str] = Field(
        default=None,
        description="Nếu is_meta_inquiry=true: 1-2 câu giải thích ngắn gọn, dân dã về khái niệm/thông số đó (kèm lợi ích thực tế nếu có), sau đó BẮT BUỘC đặt lại câu hỏi khéo léo để tiếp tục lấy thông tin (VD 'Dạ Inverter giúp tiết kiệm điện ạ. Nhà mình định mua máy tầm giá bao nhiêu?')."
    )
    is_policy_question: bool = Field(
        default=False,
        description="True nếu khách hỏi về CHÍNH SÁCH/VẬN HÀNH của cửa hàng: giờ mở cửa, tổng đài/cách liên hệ, địa chỉ, cách đặt hàng online, giao hàng, hình thức thanh toán/trả góp, hoàn tiền, chính sách bảo hành/đổi trả nói chung, khiếu nại, nội quy, hoặc về DỮ LIỆU CÁ NHÂN/quyền riêng tư (thu thập gì, lưu bao lâu, chia sẻ cho ai, cách xóa dữ liệu). KHÔNG bật nếu khách hỏi về thông số kỹ thuật (đó là is_meta_inquiry) hoặc hỏi bảo hành của MỘT sản phẩm cụ thể đang được tư vấn."
    )
    is_chitchat: bool = Field(
        default=False,
        description="True cho xã giao, câu hỏi kiến thức phổ thông hoặc yêu cầu ngoài mua sắm khi khách không có nhu cầu sản phẩm. Không coi các yêu cầu này là unsupported_product."
    )
    smalltalk_reply: Optional[str] = Field(
        default=None,
        description="Với xã giao hoặc câu hỏi kiến thức ngắn: trả lời trực tiếp, tối đa 2 câu/60 từ và kết thúc bằng một câu chuyển nhẹ về nhu cầu mua sắm. Với yêu cầu tạo nội dung như viết code, để null."
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
    wants_comparison: bool = Field(
        default=False,
        description="True nếu khách có ý định xem nhiều lựa chọn, so sánh, phân tích các mẫu khác nhau (VD: 'so sánh', 'có mấy loại', 'xem các option', 'mẫu nào tốt nhất'). False nếu khách chỉ hỏi một nhu cầu chung chung."
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
                            db_path: Optional[str] = None, addr: str = "anh/chị",
                            self_term: str = "em") -> Dict[str, Any]:
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

    # Câu hỏi chính sách cửa hàng (chỉ tin keyword khi không kèm nhu cầu sản phẩm cụ thể)
    is_policy_question = kw_policy(query) and not matched_category
    if is_policy_question:
        is_meta_inquiry = False

    # Xã giao/ngoài chủ đề (fallback thô: câu ngắn mở đầu bằng lời chào, không chứa nhu cầu)
    flat_q = strip_accents(query_lower).strip()
    is_chitchat = (not matched_category and not budget_max and not matched_brand
                   and not is_meta_inquiry and not is_policy_question and len(query.split()) <= 6
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
    if is_meta_inquiry or is_policy_question:
        needs_clarification = False
    elif not matched_category and len(query.split()) < 6 and not replying_to_clarify and not priority_features:
        needs_clarification = True
        clarification_questions = [
            f"{addr.capitalize()} đang quan tâm đến dòng sản phẩm nào trong các danh mục hiện có ({', '.join(categories[:5])}...)?",
            f"Mức ngân sách dự kiến của {addr} khoảng bao nhiêu để {self_term} hỗ trợ sàng lọc?"
        ]
    elif matched_category and not budget_max and not priority_features and len(query.split()) < 7 and not replying_to_clarify:
        needs_clarification = True
        clarification_questions = [
            f"{addr.capitalize()} tìm mua {matched_category} phục vụ cho nhu cầu hoặc mục đích sử dụng chính là gì?",
            f"Ngân sách tối đa {addr} dự kiến đầu tư cho sản phẩm này là bao nhiêu?"
        ]

    intent = {
        "is_meta_inquiry": is_meta_inquiry,
        "is_policy_question": is_policy_question,
        "is_chitchat": is_chitchat,
        "smalltalk_reply": None,
        "category": matched_category,
        "transition_message": None,
        "unsupported_product": None,
        "related_categories": [],
        "budget_max": budget_max,
        "brand": matched_brand,
        "priority_features": priority_features,
        "wants_comparison": False,
        "assumptions": [],
        "declines_more_info": kw_declines(query),
        "needs_custom_query": False,
        "needs_clarification": needs_clarification,
        "clarification_questions": clarification_questions
    }
    return normalize_intent_scope(intent, query, categories)


_SCHEMA_HINT = (
    '{"is_meta_inquiry": bool, "meta_reply": string|null, "is_policy_question": bool, '
    '"is_chitchat": bool, "smalltalk_reply": string|null, '
    '"category": string|null, "transition_message": string|null, "unsupported_product": string|null, '
    '"related_categories": string[], "budget_max": number|null, '
    '"brand": string|null, "priority_features": string[], "wants_comparison": bool, "assumptions": string[], '
    '"declines_more_info": bool, "needs_custom_query": bool, "needs_clarification": bool, '
    '"clarification_questions": string[]}'
)


def extract_intent(query: str, history: Optional[List[Dict[str, str]]] = None,
                   llm=None, db_path: Optional[str] = None, addr: str = "anh/chị",
                   self_term: str = "em") -> Dict[str, Any]:
    """Trích ý định qua DeepSeek (LLMClient.complete_json). Lỗi/không có llm -> fallback heuristic."""
    if llm is None:
        log.info("intent: không có LLM -> dùng fallback heuristic")
        return extract_intent_fallback(query, history, db_path, addr=addr, self_term=self_term)
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
            "- unsupported_product CHỈ dùng cho MẶT HÀNG hữu hình khách muốn mua nhưng catalog không có.\n"
            "- Yêu cầu tạo nội dung/thực hiện tác vụ ngoài mua sắm (viết code, dịch, giải bài tập): "
            "is_chitchat=true, unsupported_product=null, smalltalk_reply=null; hệ thống sẽ chuyển hướng ngắn.\n"
            "- Câu hỏi kiến thức ngắn ngoài mua sắm (VD 'kinh tế chính trị là gì?'): "
            "is_chitchat=true và BẮT BUỘC điền smalltalk_reply trả lời đúng trọng tâm, tối đa 2 câu/60 từ; "
            "không gọi đó là sản phẩm, không giảng giải dài và BẮT BUỘC kết bằng một câu chuyển nhẹ "
            "sang nhu cầu mua thiết bị học tập/làm việc.\n"
            "- is_meta_inquiry=true khi khách hỏi khái niệm/thông số LIÊN QUAN SẢN PHẨM "
            "(OLED, Inverter, dung tích...) hoặc hỏi tổng quan catalog. BẮT BUỘC điền meta_reply ngắn gọn.\n"
            "- wants_comparison=true khi khách chủ động yêu cầu đưa ra nhiều sự lựa chọn hoặc so sánh (VD 'so sánh', 'có những option nào', 'các dòng máy'). False nếu khách chỉ nhờ tư vấn chung.\n"
            "- needs_clarification=true khi KHÁCH TRẢ LỜI QUÁ CHUNG CHUNG và bạn cần hỏi thêm để lọc sản phẩm (mục đích, bối cảnh người dùng, ngân sách). TUYỆT ĐỐI KHÔNG bật cờ này nếu khách đang hỏi ngược lại bạn (đó là is_meta_inquiry). False nếu khách vừa trả lời đủ hoặc từ chối bổ sung.\n"
            "- clarification_questions: 1-2 câu hỏi NGẮN, tự nhiên như người bán hàng thật, bám đúng bối cảnh "
            "khách vừa kể. TUYỆT ĐỐI không hỏi lại điều khách đã nói hoặc điều trợ lý đã hỏi trong lịch sử.\n"
            "- assumptions: các suy đoán bạn tự rút ra mà khách không nói rõ, ghi ngắn gọn.\n"
            "- declines_more_info=true nếu khách né/từ chối cung cấp thêm ('gợi ý đại', 'gì cũng được', "
            "'chọn giúp anh/chị',...).\n"
            "- needs_custom_query=true khi khách ràng buộc theo THÔNG SỐ hoặc cách xếp hạng đặc biệt "
            "(dung tích/kích thước/số cửa/'ít tốn điện nhất'/'nhẹ nhất'...) — lọc cơ bản ngành+giá+hãng "
            "không đáp ứng được.\n"
            "- is_policy_question=true khi khách hỏi về CHÍNH SÁCH/VẬN HÀNH cửa hàng: giờ mở/đóng cửa, "
            "tổng đài/cách liên hệ, địa chỉ, cách đặt hàng online, thời gian/phí giao hàng, phí lắp đặt "
            "và vật tư, dịch vụ vệ sinh/sửa chữa, hình thức thanh toán/trả góp, hoàn tiền, phí đổi trả "
            "(hư gì đổi nấy, 1 đổi 1), chính sách bảo hành/đổi trả, khiếu nại, nội quy, hoặc về DỮ LIỆU "
            "CÁ NHÂN/quyền riêng tư (shop thu thập gì, lưu bao lâu, chia sẻ cho ai, cách xóa "
            "dữ liệu/tài khoản, bảo mật thông tin). KHÔNG nhầm với "
            "is_meta_inquiry (giải thích thông số/khái niệm) và KHÔNG nhầm với chitchat. Nếu khách hỏi "
            "bảo hành/giá của MỘT sản phẩm cụ thể đang tư vấn thì KHÔNG bật cờ này.\n"
            "- Nếu câu vừa chào vừa nêu nhu cầu ('chào em, cần mua tủ lạnh') thì KHÔNG phải chitchat. "
            "Nếu khách hỏi về MỘT nhóm sản phẩm cụ thể, gán trực tiếp category tương ứng.\n"
            f"- Trong mọi câu chữ hướng tới khách (clarification_questions, transition_message, "
            f"smalltalk_reply, meta_reply): xưng '{self_term}' và gọi khách là '{addr}' (không dùng 'bạn')."
        )
        hist_str = ""
        for m in (history or []):
            role = "User" if m.get("role") == "user" else "Assistant"
            hist_str += f"{role}: {m.get('content')}\n"
        user = f"Lịch sử:\n{hist_str or 'Không có'}\n\nCâu hỏi mới: {query}"
        raw = llm.complete_json(system, user, _SCHEMA_HINT)
        log.info("intent: trích qua LLM thành công")
        intent = IntentSchema(**{
            k: raw[k] for k in IntentSchema.model_fields if k in raw
        }).model_dump()
        categories = get_catalog_metadata(db_path)["categories"]
        return normalize_intent_scope(intent, query, categories)
    except Exception as e:
        log.warning("intent: LLM lỗi (%s) -> dùng fallback heuristic", e)
        return extract_intent_fallback(query, history, db_path, addr=addr, self_term=self_term)


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
