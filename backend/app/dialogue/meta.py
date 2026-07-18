from __future__ import annotations

import re

from app.advice.provenance import format_vnd
from app.catalog.category_config import CATEGORY_CONFIGS, config_for
from app.catalog.loader import ProductStore
from app.nlu.preprocess import declined_clarification, strip_accents
from app.schemas import NeedProfile


def _flat(value: str) -> str:
    return " ".join(strip_accents(value.lower()).split())


_TAIL = r"(?:\s+(?:em|anh|chi|ban|shop|ad|a|nhe|nha|nhieu|lam|nghen))*[\s!.,~]*"

_GREETING_RE = re.compile(
    rf"^(?:da[, ]+)?(?:xin chao|chao (?:em|anh|chi|ban|shop|ad)|hello|hi|alo){_TAIL}$"
)
_THANKS_RE = re.compile(
    rf"^(?:da[, ]+|ok(?:e|ie)?[, ]+)?(?:cam on|camon|thanks?|thank you|tks|cam ta){_TAIL}$"
)
# Yêu cầu có chủ ngữ chỉ bot để không nuốt câu hỏi về sản phẩm ("máy này có chức năng gì").
_CAPABILITY_RE = re.compile(
    r"\b(?:ban|em|shop|bot|tro ly|he thong)\s+"
    r"(?:co the |co |lam |giup |tu van |ho tro |tra loi |ban )*"
    r"(?:duoc )?(?:nhung |cac )?"
    r"(?:gi|cai gi|chuc nang (?:gi|nao)|(?:san pham|mat hang|nhom hang|nganh hang) (?:gi|nao))\b"
)
_DONT_KNOW_RE = re.compile(
    r"\b(?:(?:khong|chua|ko|kho)\s*(?:biet|ro|chac|nghi ra|quyet dinh)(?:\s+(?:nua|lam))?|"
    r"chiu(?:\s+thoi)?|kho noi|(?:dang )?phan van|chua nghi toi|tuy(?!\s+nhien)|bo qua)\b"
)
_ASK_OPTIONS_RE = re.compile(
    r"\b(?:"
    r"co (?:nhung |cac )?(?:lua chon|option|muc|loai|mau|kieu|phan khuc|tam gia|muc gia)(?:\s+\w+){0,2} nao|"
    r"(?:nhung|cac) (?:lua chon|option|muc|loai|mau) nao|"
    r"(?:thuong|moi nguoi|nguoi ta|da so|so dong)(?:\s+\w+){0,3} chon (?:gi|the nao|loai nao|muc nao)|"
    r"(?:muc|tam|khoang) (?:gia )?(?:bao nhieu|nao) (?:thi |la )?(?:hop ly|pho bien|vua|du)|"
    r"bao nhieu (?:thi |la )?(?:hop ly|du|vua|pho bien)"
    r")\b"
)

_SLOT_OPTION_HINTS = {
    "kiểu dáng": "ngăn đá trên hoặc ngăn đá dưới",
    "người dùng": "trẻ em, người lớn hoặc người tập thể thao",
    "mục đích": "văn phòng, chơi game hoặc đồ họa",
}


def detect_meta_intent(message: str, *, stage: str, pending_slot: str | None) -> str | None:
    """Phân loại các phản hồi 'meta' không mang dữ kiện nhu cầu.

    Trả về một trong: greeting, thanks, capability, ask_options, dont_know —
    hoặc None nếu tin nhắn nên đi vào pipeline trích xuất nhu cầu bình thường.
    """
    flat = _flat(message)
    if not flat:
        return None
    if _GREETING_RE.fullmatch(flat):
        return "greeting"
    if _THANKS_RE.fullmatch(flat):
        return "thanks"
    if _CAPABILITY_RE.search(flat):
        return "capability"
    if stage == "collecting" and pending_slot:
        if _ASK_OPTIONS_RE.search(flat):
            return "ask_options"
        # Chỉ coi là "không biết" khi câu ngắn và không kèm dữ kiện nào khác
        # ("không biết, chắc tầm 5 triệu" phải đi vào parser lấy ngân sách).
        if (len(flat) <= 45 and not any(ch.isdigit() for ch in flat)
                and not declined_clarification(message)
                and _DONT_KNOW_RE.search(flat)):
            return "dont_know"
    return None


def _category_listing() -> str:
    return ", ".join(cfg.display.lower() for cfg in CATEGORY_CONFIGS.values())


def greeting_reply() -> str:
    return (f"Dạ em chào anh/chị ạ! Em là trợ lý tư vấn điện máy, có thể giúp anh/chị chọn: "
            f"{_category_listing()}. Anh/chị đang cần tìm sản phẩm nào ạ?")


def thanks_reply() -> str:
    return ("Dạ em cảm ơn anh/chị ạ! Nếu cần tư vấn thêm hoặc so sánh sản phẩm nào, "
            "anh/chị cứ nhắn em nhé.")


def capability_reply() -> str:
    return (f"Dạ em là trợ lý tư vấn điện máy ạ. Em có thể giúp anh/chị: chọn sản phẩm theo nhu cầu "
            f"và ngân sách trong các nhóm {_category_listing()}; so sánh các máy với nhau; "
            "và giải thích thông số từng máy. Anh/chị đang quan tâm nhóm nào ạ?")


def _slot_question(category: str, slot: str) -> str | None:
    return next((s.question for s in config_for(category).ask_slots if s.slot == slot), None)


def _budget_options_reply(category: str, store: ProductStore) -> str | None:
    prices = sorted(int(p.price.value) for p in store.by_category(category) if p.price.available)
    if not prices:
        return None
    display = config_for(category).display.lower()
    mid = prices[len(prices) // 2]
    return (f"Dạ {display} bên em có giá từ {format_vnd(prices[0])} đến {format_vnd(prices[-1])}, "
            f"phổ biến quanh mức {format_vnd(mid)} ạ. Anh/chị muốn em lọc trong tầm giá nào, "
            "hay nếu chưa chắc thì anh/chị nói 'bỏ qua' để em gợi ý chung ạ?")


def options_reply(profile: NeedProfile, slot: str, store: ProductStore) -> str:
    """Trả lời câu hỏi ngược 'có những lựa chọn nào?' bằng dữ liệu thật của slot đang hỏi."""
    if profile.category is None:
        return capability_reply()
    cfg = config_for(profile.category)
    if slot == "ngân sách":
        reply = _budget_options_reply(profile.category, store)
        if reply:
            return reply
    if slot == "ưu tiên" and cfg.pref_lexicon:
        choices = ", ".join(cfg.pref_lexicon)
        return (f"Dạ với {cfg.display.lower()}, mọi người thường ưu tiên: {choices}. "
                "Anh/chị quan tâm điều nào nhất ạ?")
    hint = _SLOT_OPTION_HINTS.get(slot)
    if hint:
        return (f"Dạ về '{slot}', anh/chị có thể chọn: {hint} ạ. "
                "Anh/chị thấy hợp phương án nào ạ?")
    question = _slot_question(profile.category, slot) or "Anh/chị cho em xin thông tin đó nhé?"
    return (f"Dạ anh/chị cứ ước chừng thôi cũng được ạ. {question} "
            "(Nếu chưa chắc, anh/chị nói 'bỏ qua' để em gợi ý chung ạ.)")
