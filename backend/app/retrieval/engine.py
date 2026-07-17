from __future__ import annotations
from app.schemas import NeedProfile, Recommendation
from app.catalog.loader import ProductStore
from app.retrieval.filters import apply_hard_filters
from app.retrieval.scoring import score_products, select_top3, why_not_group
from app.retrieval.embed import semantic_scores

_SEMANTIC_WEIGHT = 0.3


def query_from_profile(profile: NeedProfile) -> str:
    parts = list(profile.prefs) + list(profile.demographics.values())
    return " ".join(parts)


class RetrievalEngine:
    def __init__(self, store: ProductStore):
        self.store = store

    def recommend(self, profile: NeedProfile) -> Recommendation:
        cands = self.store.by_category(profile.category)
        filtered = apply_hard_filters(cands, profile)
        scored = score_products(filtered, profile)
        sem = semantic_scores(query_from_profile(profile), filtered)
        if sem:
            for i, sp in enumerate(scored):
                bonus = _SEMANTIC_WEIGHT * sem.get(i, 0.0)
                sp.score += bonus
                if bonus > 0:
                    sp.breakdown["_semantic"] = bonus
        top3 = select_top3(scored)
        excluded = why_not_group(filtered, profile)
        return Recommendation(top3=top3, excluded=excluded, assumptions=list(profile.assumptions))
