"""Suy ra đại từ xưng hô phù hợp để gọi khách, thay vì hardcode 'bạn'/'anh/chị'.

Quy ước: khách xưng gì với chính mình ở ngôi thứ nhất ('em muốn mua...', 'cô hỏi
chút...') thì nhân viên (bot) gọi lại khách đúng từ đó ('em ơi', 'dạ cô...').
Không suy ra được thì dùng mặc định trung tính 'anh/chị' — KHÔNG dùng 'bạn' vì
không đồng nhất với giọng lễ phép 'dạ/ạ' + xưng 'em' của bot xuyên suốt codebase.
"""
from __future__ import annotations

import re
from typing import Optional

DEFAULT_ADDRESS = "anh/chị"
DEFAULT_SELF = "em"

# Xưng hô của bot phải ĐỐI ỨNG với cách gọi khách, không cố định 'em': khách là
# 'ông/bà/bác/cô/chú' (vai vế trên) thì bot xưng 'cháu', không xưng 'em' (chỉ hợp
# khi khách là 'anh/chị').
_SELF_TERM_MAP = {
    "ông": "cháu", "bà": "cháu", "bác": "cháu", "cô": "cháu", "chú": "cháu",
    "anh": "em", "chị": "em", "em": "em",
}

# Đại từ nhân xưng khách có thể tự dùng để chỉ chính mình ở đầu câu. Khi khách
# tự xưng bằng một trong các từ này, nhân viên gọi khách lại đúng bằng từ đó.
_SELF_PRONOUNS = ("ông", "bà", "cô", "chú", "bác", "anh", "chị", "em", "cháu", "con")

# "tôi"/"mình" không mang thông tin tuổi/vai vế -> giữ mặc định trung tính, không suy đoán.
_NEUTRAL_PRONOUNS = ("tôi", "mình")

_TRIGGER_PREFIXES = ("dạ", "vâng", "cho", "giúp", "nhờ", "cho phép")

_PRONOUN_PATTERN = re.compile(
    r"(?:^|[.!?,;\n]|(?:" + "|".join(_TRIGGER_PREFIXES) + r")\s+)\s*("
    + "|".join(_SELF_PRONOUNS + _NEUTRAL_PRONOUNS)
    + r")\b(?!\s*(?:ơi|ấy))",
    re.IGNORECASE,
)


def detect_customer_address(text: str) -> Optional[str]:
    """Tìm đại từ khách tự xưng ở ngôi thứ nhất trong câu vừa gõ.

    Trả None nếu không suy ra được rõ ràng (câu không mở đầu bằng đại từ xưng hô)
    hoặc khách dùng 'tôi'/'mình' (trung tính, không đổi cách gọi hiện tại)."""
    if not text:
        return None
    match = _PRONOUN_PATTERN.search(text.strip().lower())
    if not match:
        return None
    pronoun = match.group(1)
    if pronoun in _NEUTRAL_PRONOUNS:
        return None
    return pronoun


def resolve_address(query: str, previous: Optional[str] = None) -> str:
    """Cách gọi khách cho lượt hiện tại: ưu tiên đại từ vừa phát hiện, giữ nguyên
    lựa chọn trước đó trong phiên nếu lượt này không xưng gì mới, mặc định trung tính."""
    detected = detect_customer_address(query)
    if detected:
        return detected
    return previous or DEFAULT_ADDRESS


def resolve_self_term(addr: str) -> str:
    """Bot tự xưng gì cho khớp với cách gọi khách (VD gọi khách 'ông' thì xưng 'cháu')."""
    return _SELF_TERM_MAP.get(addr, DEFAULT_SELF)
