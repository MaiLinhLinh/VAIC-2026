from __future__ import annotations
from typing import Callable
from app.schemas import Recommendation, NeedProfile, AdviceResult
from app.llm.client import LLMClient
from app.advice.provenance import build_fact_card
from app.advice.generate import advice_prompt, generate_advice
from app.advice.verify import allowed_numbers, line_is_grounded, verify_advice


def stream_advice(reco: Recommendation, profile: NeedProfile, llm: LLMClient,
                  emit: Callable[[str], None]) -> tuple[AdviceResult, bool]:
    """generate_advice + verify_advice, but each line of the reply is emitted via
    emit() AS SOON AS the LLM finishes it AND its numbers verify against the fact
    cards (line-level fail-closed: a line with an unsourced number stops emission;
    the endpoint's final payload then replaces whatever the client displayed).

    Returns (advice, streamed) — streamed=True iff the FULL message was emitted
    live, i.e. every line passed verification. Falls back to the blocking path
    (nothing emitted, streamed=False) if the LLM stream fails.
    """
    if not reco.top3:
        # Deterministic "no match" message, no LLM call — let the endpoint deliver it.
        return generate_advice(reco, profile, llm), False

    cards = [build_fact_card(sp, profile) for sp in reco.top3]
    system, user = advice_prompt(reco, profile, cards)
    allowed = allowed_numbers(cards)
    parts: list[str] = []
    buf = ""
    emitting = True

    def push(line: str) -> None:
        nonlocal emitting
        if emitting and line_is_grounded(line, allowed):
            emit(line)
        else:
            emitting = False

    try:
        for token in llm.stream_text(system, user):
            parts.append(token)
            buf += token
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                push(line + "\n")
        if buf:
            push(buf)
    except Exception:
        return verify_advice(generate_advice(reco, profile, llm)), False

    advice = verify_advice(AdviceResult(message="".join(parts), cards=cards,
                                        assumptions=reco.assumptions, warnings=[]))
    return advice, emitting
