from __future__ import annotations
from functools import lru_cache
from app.schemas import Product
from app.config import get_settings

_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache
def _model():
    from sentence_transformers import SentenceTransformer  # lazy, optional
    return SentenceTransformer(_MODEL_NAME)


def semantic_scores(query: str, products: list[Product]) -> dict[int, float]:
    if not get_settings().enable_embeddings or not query.strip() or not products:
        return {}
    try:
        import numpy as np
        model = _model()
        docs = [p.spec_doc or p.display_name for p in products]
        emb = model.encode([query] + docs, normalize_embeddings=True)
        qv, dv = emb[0], emb[1:]
        sims = dv @ qv
        lo, hi = float(sims.min()), float(sims.max())
        span = hi - lo or 1.0
        return {i: float((s - lo) / span) for i, s in enumerate(sims)}
    except Exception:
        return {}   # thiếu thư viện/model -> bỏ qua, deterministic vẫn chạy
