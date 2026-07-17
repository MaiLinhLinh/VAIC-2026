from __future__ import annotations
from typing import Literal
from app.schemas import NeedProfile, ScoredProduct
from app.catalog.loader import ProductStore
from app.retrieval.engine import RetrievalEngine
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


def describe_tradeoff(cheaper: ScoredProduct, current_price: int) -> str:
    delta = current_price - int(cheaper.product.price.value)
    if delta <= 0:
        return f"Máy {cheaper.product.display_name}: {format_vnd(int(cheaper.product.price.value))}."
    return (f"Xuống {cheaper.product.display_name} còn {format_vnd(int(cheaper.product.price.value))} "
            f"— rẻ hơn khoảng {format_vnd(delta)}.")
