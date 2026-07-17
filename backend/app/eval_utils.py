from __future__ import annotations
from app.nlu.parser import parse_need
from app.llm.client import LLMClient
from app.catalog.loader import ProductStore
from app.retrieval.engine import RetrievalEngine
from app.advice.generate import generate_advice
from app.advice.verify import verify_advice, is_grounded


def evaluate(scenarios: list[dict], llm: LLMClient, store: ProductStore) -> dict:
    n = len(scenarios)
    cat_ok = bud_ok = 0
    pref_hit = pref_total = 0
    halluc = reco_count = 0
    for sc in scenarios:
        prof = parse_need(sc["message"], llm)
        if prof.category == sc.get("expect_category"):
            cat_ok += 1
        if prof.budget_max == sc.get("expect_budget_max"):
            bud_ok += 1
        expected = set(sc.get("expect_prefs") or [])
        pref_total += len(expected)
        pref_hit += len(expected & set(prof.prefs))
        if prof.category:
            reco = RetrievalEngine(store).recommend(prof)
            advice = verify_advice(generate_advice(reco, prof, llm))
            if reco.top3:
                reco_count += 1
                if not is_grounded(advice):
                    halluc += 1
    return {
        "n": n,
        "category_acc": cat_ok / n if n else 0.0,
        "budget_acc": bud_ok / n if n else 0.0,
        "pref_recall": pref_hit / pref_total if pref_total else 1.0,
        "hallucination_rate": halluc / reco_count if reco_count else 0.0,
    }
