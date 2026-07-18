from __future__ import annotations

import re
import unicodedata


_CATEGORY_ALIASES = {
    "tu_lanh": ("tu lanh", "refrigerator"),
    "may_say": ("may say",),
    "may_rua_chen": ("may rua chen", "rua chen", "dishwasher"),
    "tu_mat": ("tu mat", "tu dong", "freezer"),
    "dong_ho": ("dong ho", "smartwatch", "smart watch", "watch"),
    "man_hinh": ("man hinh", "monitor", "screen"),
}

_NUMBER_WORDS = {
    "mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5,
    "lam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9, "muoi": 10,
}
_NUMBER_TOKEN = (
    r"(?:\d{1,2}|muoi(?:\s+(?:mot|hai|ba|bon|tu|lam|nam|sau|bay|tam|chin))?"
    r"|mot|hai|ba|bon|tu|nam|sau|bay|tam|chin)"
)

_CONSTRAINT_ALIASES = (
    ("số người", ("so nguoi", "so thanh vien", "so nguoi su dung")),
    ("khối lượng", ("khoi luong", "khoi luong say", "khoi luong tai chinh")),
    ("dung tích", ("dung tich", "dung tich tong")),
    ("người dùng", ("nguoi dung", "doi tuong su dung")),
    ("mục đích", ("muc dich", "muc dich su dung", "nhu cau su dung")),
    ("kích thước", ("kich thuoc", "kich thuoc man hinh", "kich co man hinh")),
    ("kiểu dáng", ("kieu dang", "loai tu")),
)

_PURPOSES = (
    ("chơi game", r"\b(?:choi game|gaming|game)\b"),
    ("văn phòng", r"\b(?:van phong|office|cong viec van phong)\b"),
    ("đồ họa", r"\b(?:do hoa|thiet ke|design|chinh sua anh|edit (?:anh|video))\b"),
)

_DECLINE_RE = re.compile(
    r"\b(?:(?:sao|gi|mau nao|loai nao)(?:\s+cung)?\s+duoc|"
    r"tuy(?:\s+theo)?\s+(?:em|ban|shop)|"
    r"(?:cu\s+)?(?:goi y|tu van)(?:\s+(?:dai|giup|ho|luon|di))|"
    r"(?:khong biet|ko biet|ko bt|khong ro).{0,60}"
    r"(?:goi y|tu van|tham khao|mau phu hop|loai phu hop))\b"
)


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    unaccented = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return unaccented.replace("đ", "d").replace("Đ", "D")


def _flat(value: str) -> str:
    return " ".join(strip_accents(value.lower()).split())


def expand_shorthand(value: str) -> str:
    replacements = (
        (r"(\d+(?:[.,]\d+)?)\s*(?:tr|trieu|triệu|t|củ|cu)\b", r"\1 triệu"),
        (r"(\d+)\s*(?:k|nghin|nghìn)\b", r"\1 nghìn"),
        (r"(\d+)\s*m2\b", r"\1 m²"),
        (r"(\d+(?:[.,]\d+)?)\s*hp\b", r"\1 HP"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.I)
    return value


def _to_vnd(number: str, unit: str) -> int:
    value = float(number.replace(",", "."))
    unit = _flat(unit)
    multiplier = 1_000_000 if unit.startswith("tri") or unit in {"t", "tr"} else 1_000
    return int(value * multiplier)


def parse_budget_vnd(value: str) -> tuple[int | None, int | None]:
    text = value.lower()
    unit = r"(tri[eệ]u|tr|k|ngh[iì]n)"
    money_range = re.search(
        rf"(\d+(?:[.,]\d+)?)\s*{unit}?\s*(?:[-–]|đến|den|tới|toi)\s*"
        rf"(\d+(?:[.,]\d+)?)\s*{unit}",
        text,
    )
    if money_range:
        low_unit = money_range.group(2) or money_range.group(4)
        return (
            _to_vnd(money_range.group(1), low_unit),
            _to_vnd(money_range.group(3), money_range.group(4)),
        )

    money = re.search(rf"(\d+(?:[.,]\d+)?)\s*{unit}\b", text)
    if not money:
        return None, None
    amount = _to_vnd(money.group(1), money.group(2))
    flat = _flat(text)
    upper_cues = r"\b(?:duoi|khong qua|toi da|tro xuong|quay dau|do lai|tro lai)\b"
    lower_cues = r"\b(?:tren|hon|toi thieu|it nhat|tu)\s+(?:khoang\s+)?\d|\btro len\b"
    if re.search(upper_cues, flat):
        return None, amount
    if re.search(lower_cues, flat):
        return amount, None
    return None, amount


def detect_category(value: str) -> str | None:
    flat = _flat(value)
    return next(
        (code for code, aliases in _CATEGORY_ALIASES.items() if any(alias in flat for alias in aliases)),
        None,
    )


def canonical_constraint_key(key: str) -> str:
    normalized = _flat(key)
    for canonical, aliases in _CONSTRAINT_ALIASES:
        if normalized in aliases:
            return canonical
    return key


def _first_positive_match(flat: str, pattern: str | re.Pattern[str]):
    for match in re.finditer(pattern, flat):
        prefix = flat[max(0, match.start() - 35):match.start()]
        if not re.search(r"\b(?:khong|chua)\b(?:\s+\w+){0,3}\s*$", prefix):
            return match
    return None


def _number_value(token: str) -> int | None:
    token = " ".join(token.split())
    if token.isdigit():
        return int(token)
    parts = token.split()
    return 10 + _NUMBER_WORDS.get(parts[1], 0) if len(parts) == 2 else _NUMBER_WORDS.get(token)


def parse_people_count(value: str, allow_bare: bool = False) -> tuple[int, int] | None:
    flat = _flat(value)
    if allow_bare and re.fullmatch(_NUMBER_TOKEN, flat):
        count = _number_value(flat)
        return (count, count) if count is not None and 1 <= count <= 30 else None

    match = _first_positive_match(
        flat,
        re.compile(
            rf"(?<!\d\s)\b(?P<low>{_NUMBER_TOKEN})\s*"
            rf"(?:(?:[-–]\s*|(?:den|toi)\s+)(?P<high>{_NUMBER_TOKEN}))?"
            rf"\s*(?:nguoi|thanh vien)\b"
        ),
    )
    if not match:
        return None
    low = _number_value(match.group("low"))
    high = _number_value(match.group("high")) if match.group("high") else low
    return (low, high) if low is not None and high is not None and 1 <= low <= high <= 30 else None


def parse_screen_size_inches(value: str) -> tuple[float | None, float | None] | float | None:
    flat = _flat(value)
    match = _first_positive_match(flat, r"\b(\d+(?:[.,]\d+)?)\s*inch\b")
    if not match:
        return None
    size = float(match.group(1).replace(",", "."))
    context = flat[max(0, match.start() - 30):match.end() + 20]
    if re.search(r"\b(?:toi thieu|it nhat|tu)\b.*inch|inch\s+tro len\b", context):
        return size, None
    if re.search(r"\b(?:toi da|duoi|nho hon|khong qua)\b.*inch|inch\s+tro xuong\b", context):
        return None, size
    return size


def parse_monitor_purpose(value: str) -> str | None:
    flat = _flat(value)
    return next((purpose for purpose, pattern in _PURPOSES if _first_positive_match(flat, pattern)), None)


def prefers_large_screen(value: str) -> bool:
    return bool(_first_positive_match(
        _flat(value),
        r"\b(?:cang (?:to|lon) cang tot|(?:to|lon) nhat|man hinh (?:to|lon)|"
        r"(?:kich thuoc|kich co|size) (?:to|lon))\b",
    ))


def declined_clarification(value: str) -> bool:
    return bool(_DECLINE_RE.search(_flat(value)))


def extract_explicit_demographics(value: str) -> dict[str, str]:
    """Extract only high-confidence facts; the LLM handles the long tail."""
    flat = _flat(value)
    result: dict[str, str] = {}

    age = _first_positive_match(flat, r"\b(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*tuoi\b")
    if age:
        low, high = int(age.group(1)), int(age.group(2) or age.group(1))
        if 0 <= low <= high <= 120:
            result["độ tuổi"] = f"{low} tuổi" if low == high else f"{low}-{high} tuổi"

    audience = (
        ("trẻ em", r"\b(?:tre em|em be|be trai|be gai|cho be|con nho)\b"),
        ("người cao tuổi", r"\b(?:nguoi cao tuoi|nguoi lon tuoi|nguoi gia|ong ba)\b"),
        ("thiếu niên", r"\b(?:thieu nien|tuoi teen)\b"),
        ("người lớn", r"\bnguoi lon\b"),
    )
    target = next((label for label, pattern in audience if _first_positive_match(flat, pattern)), None)
    if target:
        result["đối tượng"] = target

    genders = (
        ("nam", r"\b(?:nam gioi|be trai|con trai|danh cho nam|cho nam|(?:toi|minh) (?:la )?nam)\b"),
        ("nữ", r"\b(?:nu gioi|be gai|con gai|danh cho nu|cho nu|(?:toi|minh) (?:la )?nu)\b"),
    )
    found = [label for label, pattern in genders if _first_positive_match(flat, pattern)]
    if len(found) == 1:
        result["giới tính"] = found[0]

    # Only common high-confidence fallback; arbitrary explicit jobs are grounded from LLM output.
    occupations = {"giao vien": "giáo viên", "sinh vien": "sinh viên", "lap trinh vien": "lập trình viên"}
    for phrase, label in occupations.items():
        if _first_positive_match(flat, rf"\b(?:lam )?{phrase}\b"):
            result["nghề nghiệp"] = label
            break
    return result
