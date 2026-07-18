from __future__ import annotations
from typing import Literal
from app.schemas import NeedProfile, ScoredProduct
from app.catalog.loader import ProductStore
from app.retrieval.engine import RetrievalEngine
from app.retrieval.filters import apply_hard_filters
from app.retrieval.scoring import score_products
from app.catalog.category_config import config_for
from app.advice.provenance import format_vnd


def budget_alternatives(profile: NeedProfile, store: ProductStore,
                        direction: Literal["down", "up"]) -> list[ScoredProduct]:
    anchor = profile.budget_max or 0
    alt = profile.model_copy(deep=True)
    alt.budget_min = None
    if direction == "down":
        alt.budget_max = int(anchor * 0.7) if anchor else None
    else:
        alt.budget_min = int(anchor * 1.0) if anchor else None
        alt.budget_max = int(anchor * 1.4) if anchor else None
    reco = RetrievalEngine(store).recommend(alt)
    return reco.top3


def minimum_budget_options(profile: NeedProfile, store: ProductStore) -> list[ScoredProduct]:
    """Return the cheapest priced products after removing only the old budget bounds."""
    relaxed = profile.model_copy(deep=True)
    relaxed.budget_min = None
    relaxed.budget_max = None
    candidates = apply_hard_filters(store.by_category(relaxed.category), relaxed)
    scored = score_products(candidates, relaxed)

    recognized_prefs = {
        pref for pref in relaxed.prefs if pref in config_for(relaxed.category).pref_lexicon
    }
    if recognized_prefs:
        with_pref_data = [sp for sp in scored if recognized_prefs.issubset(set(sp.matched))]
        if with_pref_data:
            scored = with_pref_data

    priced = sorted(
        (sp for sp in scored if sp.product.price.available),
        key=lambda sp: (int(sp.product.price.value), -sp.score),
    )
    distinct: list[ScoredProduct] = []
    seen_names: set[str] = set()
    for sp in priced:
        if sp.product.display_name in seen_names:
            continue
        seen_names.add(sp.product.display_name)
        distinct.append(sp)
        if len(distinct) == 3:
            break
    return distinct


def describe_tradeoff(cheaper: ScoredProduct, current_price: int) -> str:
    delta = current_price - int(cheaper.product.price.value)
    if delta <= 0:
        return f"Máy {cheaper.product.display_name}: {format_vnd(int(cheaper.product.price.value))}."
    return (f"Xuống {cheaper.product.display_name} còn {format_vnd(int(cheaper.product.price.value))} "
            f"— rẻ hơn khoảng {format_vnd(delta)}.")
