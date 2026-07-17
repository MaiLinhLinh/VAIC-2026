from __future__ import annotations
from app.schemas import Product, NeedProfile, ScoredProduct, ExcludedGroup
from app.catalog.category_config import config_for


def _normalize(values: list[float], direction: str) -> dict[int, float]:
    present = [v for v in values if v is not None]
    if not present:
        return {}
    lo, hi = min(present), max(present)
    span = hi - lo
    out = {}
    for i, v in enumerate(values):
        if v is None:
            out[i] = 0.0
        elif span == 0:
            out[i] = 1.0
        else:
            frac = (v - lo) / span
            out[i] = (1 - frac) if direction == "min" else frac
    return out


def score_products(candidates: list[Product], profile: NeedProfile) -> list[ScoredProduct]:
    if not candidates:
        return []
    cfg = config_for(profile.category)
    scored = [ScoredProduct(product=p, score=0.0, breakdown={}, matched=[]) for p in candidates]
    for pref in profile.prefs:
        signals = cfg.pref_lexicon.get(pref)
        if not signals:
            continue
        for sig in signals:
            col = [p.number(sig.field) for p in candidates]
            norm = _normalize(col, sig.direction)
            for i, sp in enumerate(scored):
                contrib = sig.weight * norm.get(i, 0.0)
                if contrib > 0:
                    sp.score += contrib
                    sp.breakdown[pref] = sp.breakdown.get(pref, 0.0) + contrib
                    if pref not in sp.matched:
                        sp.matched.append(pref)
    return scored


def select_top3(scored: list[ScoredProduct]) -> list[ScoredProduct]:
    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    if len(ranked) <= 3:
        return ranked
    chosen = [ranked[0]]
    for cand in ranked[1:]:
        if len(chosen) >= 3:
            break
        brands = {c.product.brand for c in chosen}
        # ưu tiên brand khác để đa dạng; nếu vòng còn ít thì vẫn nhận
        if cand.product.brand not in brands or len(ranked) - ranked.index(cand) <= (3 - len(chosen)):
            chosen.append(cand)
    for cand in ranked[1:]:
        if len(chosen) >= 3:
            break
        if cand not in chosen:
            chosen.append(cand)
    return chosen[:3]


def why_not_group(candidates: list[Product], profile: NeedProfile) -> ExcludedGroup | None:
    cfg = config_for(profile.category)
    for rule in cfg.exclusion_rules:
        if rule.when_pref not in profile.prefs:
            continue
        bad = [p for p in candidates
               if rule.empty_means_bad and (p.specs.get(rule.field) is None or not p.specs[rule.field].available)]
        if bad:
            return ExcludedGroup(
                label=rule.label,
                reason=f"Em không đưa nhóm {rule.label} vào dù có thể rẻ hơn, "
                       f"vì anh/chị ưu tiên {rule.when_pref}.")
    return None
