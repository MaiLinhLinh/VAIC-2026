# -*- coding: utf-8 -*-
"""Fill missing crawl data (url, image, rating) into products.db category tables.

Reuses app.advice.crawler logic. Resumable: rows that already have
"url (crawl)" are skipped, so the script can be re-run safely.

Usage (from backend/):
    python scripts/fill_crawl_data.py [--limit N] [--no-detail]
"""
import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.advice.crawler import fetch_product_info, fetch_product_detail  # noqa: E402

DB_PATH = BACKEND_DIR / "app" / "agent_core" / "products.db"
LOG_PATH = BACKEND_DIR / "scripts" / "fill_crawl_data.log"

REQUEST_DELAY = 0.25  # seconds between products, keep polite
COMMIT_EVERY = 50

INVALID_PIDS = ("", "nan", "none", "null")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("fill_crawl")
# Silence per-request logs from the crawler module
logging.getLogger("crawler").setLevel(logging.WARNING)


def normalize_pid(raw) -> str | None:
    if raw is None:
        return None
    pid = str(raw).strip()
    if pid.endswith(".0"):
        pid = pid[:-2]
    if not pid or pid.lower() in INVALID_PIDS:
        return None
    return pid


def category_tables(cur) -> list[str]:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT IN ('all_products', 'sqlite_sequence')"
    )
    tables = []
    for (name,) in cur.fetchall():
        cur.execute(f'PRAGMA table_info("{name}")')
        cols = {r[1] for r in cur.fetchall()}
        if {"productidweb", "url (crawl)", "ảnh (crawl)"} <= cols:
            tables.append(name)
    return tables


def pending_pids(cur, table: str) -> list[str]:
    cur.execute(
        f'SELECT DISTINCT productidweb FROM "{table}" '
        f'WHERE productidweb IS NOT NULL '
        f'AND ("url (crawl)" IS NULL OR "url (crawl)" = \'\')'
    )
    pids = []
    seen = set()
    for (raw,) in cur.fetchall():
        pid = normalize_pid(raw)
        if pid and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def update_rows(cur, table: str, pid: str, link: str, image: str | None,
                rating: float | None) -> int:
    sets = ['"url (crawl)" = ?', '"ảnh (crawl)" = ?']
    params: list = [link, image]
    if rating is not None:
        sets.append('"rating (crawl)" = ?')
        params.append(rating)
    # Match every row form of the pid (int, float-suffixed, or text)
    params.extend([pid, f"{pid}.0", pid])
    cur.execute(
        f'UPDATE "{table}" SET {", ".join(sets)} '
        f'WHERE CAST(productidweb AS TEXT) IN (?, ?) OR TRIM(CAST(productidweb AS TEXT)) = ?',
        params,
    )
    return cur.rowcount


def pending_detail_rows(cur, table: str) -> list[tuple[str, str]]:
    """PIDs đã có URL nhưng thiếu stock/installment/review_count — cần cào lại detail."""
    cur.execute(
        f'SELECT DISTINCT productidweb, "url (crawl)" FROM "{table}" '
        f'WHERE productidweb IS NOT NULL '
        f'AND "url (crawl)" IS NOT NULL AND "url (crawl)" != \'\' '
        f'AND ("tồn kho (crawl)" IS NULL '
        f'  OR "trả góp (crawl)" IS NULL '
        f'  OR "số đánh giá (crawl)" IS NULL)'
    )
    out: list[tuple[str, str]] = []
    seen_pid: set[str] = set()
    for raw_pid, url in cur.fetchall():
        pid = normalize_pid(raw_pid)
        if not pid or pid in seen_pid:
            continue
        seen_pid.add(pid)
        out.append((pid, url))
    return out


def update_detail_rows(cur, table: str, pid: str, detail: dict) -> int:
    sets: list[str] = []
    params: list = []
    stock = detail.get("stock_status")
    inst = detail.get("installment")
    rc = detail.get("review_count")
    rating = detail.get("rating")
    if stock is not None:
        sets.append('"tồn kho (crawl)" = ?')
        params.append(stock)
    if inst is not None:
        sets.append('"trả góp (crawl)" = ?')
        params.append(inst)
    if rc is not None:
        sets.append('"số đánh giá (crawl)" = ?')
        params.append(int(rc))
    if rating is not None:
        sets.append('"rating (crawl)" = ?')
        params.append(float(rating))
    if not sets:
        return 0
    params.extend([pid, f"{pid}.0", pid])
    cur.execute(
        f'UPDATE "{table}" SET {", ".join(sets)} '
        f'WHERE CAST(productidweb AS TEXT) IN (?, ?) OR TRIM(CAST(productidweb AS TEXT)) = ?',
        params,
    )
    return cur.rowcount


def run_refetch_detail(con, cur, args) -> None:
    """Mode --refetch-detail: cào lại trang sản phẩm cho các row đã có URL."""
    work: list[tuple[str, str, str]] = []  # (table, pid, url)
    for table in category_tables(cur):
        for pid, url in pending_detail_rows(cur, table):
            work.append((table, pid, url))
    log.info("Refetch-detail pending: %d (table, pid, url) triples", len(work))
    print(f"Refetch-detail pending: {len(work)} (table, pid, url) triples", flush=True)

    crawled = 0
    updated_rows = 0
    ok = failed = 0
    # Cache theo URL — nhiều biến thể (table, pid) trùng URL trang sản phẩm
    url_cache: dict[str, dict | None] = {}

    try:
        for i, (table, pid, url) in enumerate(work, 1):
            if url in url_cache:
                detail = url_cache[url]
            else:
                if args.limit and crawled >= args.limit:
                    break
                detail = fetch_product_detail(url)
                url_cache[url] = detail
                crawled += 1
                time.sleep(REQUEST_DELAY)

            if detail:
                n = update_detail_rows(cur, table, pid, detail)
                updated_rows += n
                ok += 1
            else:
                failed += 1

            if i % COMMIT_EVERY == 0:
                con.commit()
                msg = (f"[{i}/{len(work)}] crawled={crawled} ok={ok} "
                       f"failed={failed} rows_updated={updated_rows} "
                       f"urls_cached={len(url_cache)}")
                log.info(msg)
                print(msg, flush=True)
    except KeyboardInterrupt:
        log.warning("Interrupted, committing partial progress")
    finally:
        con.commit()

    summary = (f"DONE refetch-detail crawled={crawled} ok={ok} failed={failed} "
               f"rows_updated={updated_rows} urls_cached={len(url_cache)}")
    log.info(summary)
    print(summary, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N crawled pids (0 = all)")
    parser.add_argument("--no-detail", action="store_true",
                        help="skip the detail-page crawl (no rating)")
    parser.add_argument("--refetch-detail", action="store_true",
                        help="re-crawl detail (stock/installment/review_count) "
                             "for rows that already have URL but miss the new columns")
    args = parser.parse_args()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    if args.refetch_detail:
        run_refetch_detail(con, cur, args)
        con.close()
        return

    work: list[tuple[str, str]] = []  # (table, pid)
    for table in category_tables(cur):
        for pid in pending_pids(cur, table):
            work.append((table, pid))
    log.info("Pending: %d (table, pid) pairs", len(work))
    print(f"Pending: {len(work)} (table, pid) pairs", flush=True)

    crawled = 0
    updated_rows = 0
    ok = failed = 0
    pid_cache: dict[str, tuple[str | None, str | None, float | None]] = {}

    try:
        for i, (table, pid) in enumerate(work, 1):
            if pid in pid_cache:
                link, image, rating = pid_cache[pid]
            else:
                if args.limit and crawled >= args.limit:
                    break
                link, image = fetch_product_info(pid)
                rating = None
                if link and not args.no_detail:
                    detail = fetch_product_detail(link)
                    if detail and detail.get("rating") is not None:
                        rating = detail["rating"]
                pid_cache[pid] = (link, image, rating)
                crawled += 1
                time.sleep(REQUEST_DELAY)

            if link:
                n = update_rows(cur, table, pid, link, image, rating)
                updated_rows += n
                ok += 1
            else:
                failed += 1

            if i % COMMIT_EVERY == 0:
                con.commit()
                msg = (f"[{i}/{len(work)}] crawled={crawled} ok={ok} "
                       f"failed={failed} rows_updated={updated_rows}")
                log.info(msg)
                print(msg, flush=True)
    except KeyboardInterrupt:
        log.warning("Interrupted, committing partial progress")
    finally:
        con.commit()
        con.close()

    summary = (f"DONE crawled={crawled} ok={ok} failed={failed} "
               f"rows_updated={updated_rows}")
    log.info(summary)
    print(summary, flush=True)


if __name__ == "__main__":
    main()
