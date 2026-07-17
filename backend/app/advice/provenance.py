from __future__ import annotations
from app.schemas import ScoredProduct, NeedProfile, FactCard, FactLine

_ALWAYS_MISSING = ["tồn kho", "đánh giá người dùng (review)", "trả góp"]


def format_vnd(n: int) -> str:
    return f"{n:,}".replace(",", ".") + "đ"


def build_fact_card(sp: ScoredProduct, profile: NeedProfile) -> FactCard:
    p = sp.product
    lines: list[FactLine] = []
    missing: list[str] = []

    if p.price.available:
        detail = p.price.provenance.detail if p.price.provenance else None
        lines.append(FactLine(label="Giá", value=format_vnd(int(p.price.value)),
                              source="catalog" + (f" ({detail})" if detail else "")))
        if (p.sale_price.available and p.original_price.available
                and p.sale_price.value != p.original_price.value):
            lines.append(FactLine(label="Giá gốc", value=format_vnd(int(p.original_price.value)),
                                  source="catalog"))
    else:
        missing.append("giá")

    for field, sv in p.specs.items():
        if not _relevant(field, sp):
            continue
        if sv.available and sv.value is not None:
            unit = f" {sv.unit}" if sv.unit else ""
            lines.append(FactLine(label=field, value=f"{sv.value}{unit}",
                                  source=sv.provenance.source if sv.provenance else "thông số nhà sản xuất"))
        else:
            missing.append(field)

    missing.extend(_ALWAYS_MISSING)
    return FactCard(title=f"Vì sao em đề xuất {p.display_name}?", lines=lines, missing=missing)


def _relevant(field: str, sp: ScoredProduct) -> bool:
    # spec được coi là "quyết định" nếu nó là field đứng sau một pref đã khớp
    from app.catalog.category_config import config_for
    cfg = config_for(sp.product.category_code)
    fields = set()
    for pref in sp.matched:
        for sig in cfg.pref_lexicon.get(pref, []):
            fields.add(sig.field)
    return field in fields


def facts_for_llm(cards: list[FactCard]) -> str:
    blocks = []
    for c in cards:
        rows = [f"  - {l.label}: {l.value}  [nguồn: {l.source}]" for l in c.lines]
        miss = ", ".join(c.missing)
        blocks.append(c.title + "\n" + "\n".join(rows) + f"\n  - CHƯA CÓ DỮ LIỆU: {miss}")
    return "\n\n".join(blocks)
