from __future__ import annotations
import re
import unicodedata

_CATEGORY_KEYWORDS = {
    "tu_lanh": ["tu lanh", "refrigerator"],
    "may_say": ["may say"],
    "may_rua_chen": ["rua chen", "may rua chen", "dishwasher"],
    "tu_mat": ["tu mat", "tu dong", "freezer"],
    "dong_ho": ["dong ho", "smartwatch", "smart watch", "watch"],
    "man_hinh": ["man hinh", "monitor", "screen"],
}


def strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s)
    out = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return out.replace("đ", "d").replace("Đ", "D")


def expand_shorthand(s: str) -> str:
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*(tr|trieu|triệu|t)\b", r"\1 triệu", s, flags=re.I)
    s = re.sub(r"(\d+)\s*(k|nghin|nghìn)\b", r"\1 nghìn", s, flags=re.I)
    s = re.sub(r"(\d+)\s*m2\b", r"\1 m²", s, flags=re.I)
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*hp\b", r"\1 HP", s, flags=re.I)
    return s


def _to_vnd(num: float, unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("tri") or unit in ("t", "tr"):
        return int(num * 1_000_000)
    if unit.startswith("ngh") or unit == "k":
        return int(num * 1_000)
    return int(num)


def parse_budget_vnd(s: str):
    txt = s.lower()
    rng = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(tri[eệ]u|tr|k|ngh[iì]n)", txt)
    if rng:
        u = rng.group(3)
        lo = _to_vnd(float(rng.group(1).replace(",", ".")), u)
        hi = _to_vnd(float(rng.group(2).replace(",", ".")), u)
        return (lo, hi)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(tri[eệ]u|tr|k|ngh[iì]n)\b", txt)
    if not m:
        return (None, None)
    val = _to_vnd(float(m.group(1).replace(",", ".")), m.group(2))
    if "trên" in txt or "tren" in txt or "từ" in txt:
        return (val, None)
    return (None, val)  # mặc định: 1 con số = trần ngân sách


def detect_category(s: str) -> str | None:
    flat = strip_accents(s.lower())
    for code, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in flat for kw in kws):
            return code
    return None
