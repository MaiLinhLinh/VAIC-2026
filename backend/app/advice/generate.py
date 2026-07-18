from __future__ import annotations
from app.schemas import Recommendation, NeedProfile, AdviceResult
from app.llm.client import LLMClient
from app.advice.provenance import build_fact_card, facts_for_llm

ADVICE_SYSTEM_PROMPT = (
    "Bạn là nhân viên tư vấn điện máy thân thiện, nói tiếng Việt bình dân (không dùng thuật ngữ "
    "marketing). Bạn CHỈ được dùng các con số và dữ kiện trong phần FACTS bên dưới; "
    "TUYỆT ĐỐI không bịa thêm giá, thông số, khuyến mãi, tồn kho. Nếu một thông tin nằm trong mục "
    "'CHƯA CÓ DỮ LIỆU', hãy nói thẳng 'em chưa có dữ liệu' về mục đó. "
    "Trình bày: mở đầu 1 câu, rồi liệt kê từng máy kèm 1 điểm mạnh và 1 điểm đánh đổi thật, "
    "cuối cùng giải thích ngắn gọn vì sao có nhóm sản phẩm em không đề xuất (nếu có). "
    "Giữ giọng gần gũi, không phóng đại, không ép mua."
)


def _empty_message() -> str:
    return ("Dạ em chưa tìm được máy khớp tiêu chí trong tầm giá này. "
            "Anh/chị có thể nới ngân sách hoặc bớt một ràng buộc để em tìm lại nhé?")


def advice_prompt(reco: Recommendation, profile: NeedProfile, cards: list) -> tuple[str, str]:
    # Shared by the blocking path (generate_advice) and the streaming path (streaming.py).
    facts = facts_for_llm(cards)
    excluded_txt = f"\nNhóm không đề xuất: {reco.excluded.label} — {reco.excluded.reason}" if reco.excluded else ""
    prefs_txt = ", ".join(profile.prefs) or "không nêu rõ"
    user = (f"Nhu cầu khách: ưu tiên {prefs_txt}.\n\nFACTS:\n{facts}{excluded_txt}\n\n"
            "Viết lời tư vấn theo đúng quy tắc.")
    return ADVICE_SYSTEM_PROMPT, user


def generate_advice(reco: Recommendation, profile: NeedProfile, llm: LLMClient) -> AdviceResult:
    if not reco.top3:
        return AdviceResult(message=_empty_message(), cards=[], assumptions=reco.assumptions, warnings=[])

    cards = [build_fact_card(sp, profile) for sp in reco.top3]
    system, user = advice_prompt(reco, profile, cards)
    message = llm.complete_text(system, user)
    return AdviceResult(message=message, cards=cards, assumptions=reco.assumptions, warnings=[])
