from __future__ import annotations
from app.schemas import Recommendation, NeedProfile, AdviceResult
from app.llm.client import LLMClient
from app.advice.provenance import build_fact_card, facts_for_llm, format_vnd

ADVICE_SYSTEM_PROMPT = (
    "Bạn là nhân viên tư vấn điện máy thân thiện, nói tiếng Việt bình dân (không dùng thuật ngữ "
    "marketing). Bạn CHỈ được dùng các con số và dữ kiện trong phần FACTS bên dưới; "
    "TUYỆT ĐỐI không bịa thêm giá, thông số, khuyến mãi, tồn kho. Nếu một thông tin nằm trong mục "
    "'CHƯA CÓ DỮ LIỆU', hãy nói thẳng 'em chưa có dữ liệu' về mục đó. "
    "Trình bày: mở đầu 1 câu, rồi liệt kê từng máy kèm 1 điểm mạnh và 1 điểm đánh đổi thật, "
    "cuối cùng giải thích ngắn gọn vì sao có nhóm sản phẩm em không đề xuất (nếu có). "
    "Giữ giọng gần gũi, không phóng đại, không ép mua."
)


_CONSTRAINT_LABELS = {
    "kích thước": ("màn", "inch"),
    "số người": ("nhà", "người"),
}


def _format_interval(label: str, value) -> str | None:
    if label not in _CONSTRAINT_LABELS:
        return None
    prefix, unit = _CONSTRAINT_LABELS[label]
    if isinstance(value, (int, float)):
        return f"{prefix} khoảng {value:g} {unit}"
    if not isinstance(value, list) or len(value) != 2:
        return None
    low, high = value
    if low is not None and low == high:
        return f"{prefix} {low:g} {unit}"
    if high is None:
        return f"{prefix} từ {low:g} {unit} trở lên"
    if low is None:
        return f"{prefix} tối đa {high:g} {unit}"
    return f"{prefix} khoảng {low:g}-{high:g} {unit}"


def _profile_summary(profile: NeedProfile) -> str | None:
    bits: list[str] = []
    if profile.budget_min is not None and profile.budget_max is not None:
        bits.append(f"ngân sách {format_vnd(profile.budget_min)}-{format_vnd(profile.budget_max)}")
    elif profile.budget_max is not None:
        bits.append(f"ngân sách tối đa {format_vnd(profile.budget_max)}")
    elif profile.budget_min is not None:
        bits.append(f"ngân sách từ {format_vnd(profile.budget_min)}")
    for key, value in profile.constraints.items():
        rendered = None if key.startswith("_") else _format_interval(key, value)
        if rendered:
            bits.append(rendered)
    if profile.prefs:
        bits.append("ưu tiên " + ", ".join(profile.prefs))
    return ", ".join(bits) if bits else None


def _empty_message(profile: NeedProfile) -> str:
    summary = _profile_summary(profile)
    prefix = f"Dạ với {summary}, " if summary else "Dạ "
    return (
        f"{prefix}em chưa tìm được máy khớp tiêu chí trong danh sách hiện có. "
        "Em có thể kiểm tra mức giá thấp nhất nếu giữ các tiêu chí trên, "
        "hoặc thử bớt một ràng buộc cho anh/chị ạ."
    )


def generate_advice(reco: Recommendation, profile: NeedProfile, llm: LLMClient) -> AdviceResult:
    if not reco.top3:
        return AdviceResult(message=_empty_message(profile), cards=[], assumptions=reco.assumptions, warnings=[])

    cards = [build_fact_card(sp, profile) for sp in reco.top3]
    facts = facts_for_llm(cards)
    excluded_txt = f"\nNhóm không đề xuất: {reco.excluded.label} — {reco.excluded.reason}" if reco.excluded else ""
    prefs_txt = ", ".join(profile.prefs) or "không nêu rõ"
    user = (f"Nhu cầu khách: ưu tiên {prefs_txt}.\n\nFACTS:\n{facts}{excluded_txt}\n\n"
            "Viết lời tư vấn theo đúng quy tắc.")
    message = llm.complete_text(ADVICE_SYSTEM_PROMPT, user)
    return AdviceResult(message=message, cards=cards, assumptions=reco.assumptions, warnings=[])
