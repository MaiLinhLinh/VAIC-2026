import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.catalog.crawl_clean import clean_crawl
from app.config import get_settings


def main():
    s = get_settings()
    with open(s.crawl_path, encoding="utf-8") as f:
        records = json.load(f)
    cleaned, report = clean_crawl(records)
    os.makedirs(os.path.dirname(s.crawl_cleaned_path), exist_ok=True)
    with open(s.crawl_cleaned_path, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in cleaned], f, ensure_ascii=False)
    print(f"Cleaned {report['total_in']} -> {report['total_out']} products "
          f"-> {s.crawl_cleaned_path}")
    for key in sorted(k for k in report if k not in ("total_in", "total_out")):
        print(f"  {key}: {report[key]}")


if __name__ == "__main__":
    main()
