from __future__ import annotations
import re
from app.schemas import AdviceResult, FactCard

# số có ít nhất 2 chữ số, cho phép dấu . , phân tách nghìn / thập phân
_NUM = re.compile(r"\d[\d.,]*\d")


def _canon(token: str) -> str:
    # bỏ mọi dấu . , -> chuỗi chữ số thuần (12.400.000 -> 12400000; 1,3 -> 13)
    return re.sub(r"[.,]", "", token)


def extract_numbers(text: str) -> list[str]:
    return [_canon(m.group(0)) for m in _NUM.finditer(text)]


def allowed_numbers(cards: list[FactCard]) -> set[str]:
    allowed: set[str] = set()
    for c in cards:
        for l in c.lines:
            for n in extract_numbers(l.value):
                allowed.add(n)
    return allowed


# các số "an toàn" (đời thường) không cần nguồn: 1..9 chữ số đơn, phần trăm nhỏ
_SAFE = {str(i) for i in range(0, 100)}


def verify_advice(result: AdviceResult) -> AdviceResult:
    allowed = allowed_numbers(result.cards)
    warnings = list(result.warnings)
    for n in extract_numbers(result.message):
        if n in allowed or n in _SAFE:
            continue
        warnings.append(f"Số chưa truy được nguồn: {n}")
    result.warnings = warnings
    return result


def is_grounded(result: AdviceResult) -> bool:
    return not any(w.startswith("Số chưa truy được nguồn") for w in result.warnings)
