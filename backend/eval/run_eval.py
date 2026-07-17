import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.eval_utils import evaluate
from app.catalog.loader import get_store
from app.llm.client import get_llm


def main():
    path = os.path.join(os.path.dirname(__file__), "scenarios.jsonl")
    scenarios = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    m = evaluate(scenarios, get_llm(), get_store())
    print("=== EVAL METRICS ===")
    for k, v in m.items():
        print(f"{k:20s}: {v:.3f}" if isinstance(v, float) else f"{k:20s}: {v}")


if __name__ == "__main__":
    main()
