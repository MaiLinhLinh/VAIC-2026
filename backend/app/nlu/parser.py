from __future__ import annotations
from app.schemas import NeedProfile
from app.llm.client import LLMClient
from app.nlu.preprocess import expand_shorthand, parse_budget_vnd, detect_category
from app.catalog.category_config import CATEGORY_CONFIGS

NEED_SYSTEM_PROMPT = (
    "Bạn là bộ phân tích nhu cầu mua điện máy. Chỉ trích xuất những gì khách HÀNG NÓI RÕ. "
    "TUYỆT ĐỐI không suy đoán thông tin khách chưa nêu — thông tin thiếu để trống (null), "
    "không tự bịa. category chỉ nhận một trong: "
    "tu_lanh, may_say, may_rua_chen, tu_mat, dong_ho, man_hinh (hoặc null nếu không rõ). "
    "budget tính bằng VND (số nguyên đồng). prefs là các cụm ưu tiên ngắn gọn tiếng Việt có dấu "
    "(vd: 'tiết kiệm điện', 'ít ồn', 'pin lâu', 'màn hình lớn', 'chơi game'). "
    "constraints chứa ràng buộc cứng khách nêu (vd số người, kích thước). "
    "demographics chứa suy luận nhân khẩu học CHỈ khi khách nói rõ (vd 'cho bé' -> {\"đối tượng\":\"trẻ em\"}). "
    "known liệt kê tên các trường đã điền được."
)

NEED_SCHEMA_HINT = (
    '{"category": "tu_lanh|null", "budget_min": int|null, "budget_max": int|null, '
    '"constraints": {}, "prefs": [], "demographics": {}, "known": []}'
)

_VALID_CODES = set(CATEGORY_CONFIGS.keys())
_DECLINE_PHRASES = ["gợi ý đại", "goi y dai", "sao cũng được", "sao cung duoc", "tùy em", "tuy em", "gì cũng được"]


def _to_profile(data: dict) -> NeedProfile:
    cat = data.get("category")
    if cat not in _VALID_CODES:
        cat = None
    known = list(data.get("known") or [])
    if cat is None and "category" in known:
        known.remove("category")
    return NeedProfile(
        category=cat,
        budget_min=data.get("budget_min"),
        budget_max=data.get("budget_max"),
        constraints=data.get("constraints") or {},
        prefs=data.get("prefs") or [],
        demographics=data.get("demographics") or {},
        known=known,
    )


def parse_need(message: str, llm: LLMClient, prior: NeedProfile | None = None) -> NeedProfile:
    expanded = expand_shorthand(message)
    raw = llm.complete_json(NEED_SYSTEM_PROMPT, expanded, schema_hint=NEED_SCHEMA_HINT)
    prof = _to_profile(raw)

    # Deterministic fallback: bù category & budget nếu LLM bỏ sót
    if prof.category is None:
        det = detect_category(message)
        if det:
            prof.category = det
            prof.known.append("category")
    if prof.budget_max is None and prof.budget_min is None:
        lo, hi = parse_budget_vnd(expanded)
        if hi is not None:
            prof.budget_max = hi
            prof.known.append("budget_max")
        if lo is not None:
            prof.budget_min = lo
            prof.known.append("budget_min")

    flat = message.lower()
    if any(p in flat for p in _DECLINE_PHRASES):
        prof.constraints["_khong_muon_tra_loi"] = True
        prof.known.append("_khong_muon_tra_loi")

    prof.known = list(dict.fromkeys(prof.known))
    return prior.merge(prof) if prior else prof
