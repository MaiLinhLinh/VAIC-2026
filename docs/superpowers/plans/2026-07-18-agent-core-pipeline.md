# Agent-Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khi chạy `uvicorn app.main:app`, `/api/chat` và `/api/chat/stream` trả lời bằng luồng agent_core (LangGraph `StateGraph` + `MemorySaver`), chạy DeepSeek-V4-Flash trên `products.db`, có đủ verify/guardrail + so sánh + hỏi chi tiết, và giữ nguyên contract frontend.

**Architecture:** Hybrid dựng native trong `backend/app/agent_core/`. Đồ thị `intent → router → {clarify | detail | retrieve → advisor → compare → verify}`. Node dùng dict-row SQLite (14 ngành) nhưng xuất đúng schema `FactCard`/`ComparisonTable` mà frontend đang tiêu thụ. Tái dùng tối đa `DeepSeekClient`, `app.advice.verify`, `app.advice.provenance.format_vnd`/`facts_for_llm`, `app.schemas`. `main.py` chọn engine qua cờ `PIPELINE` (mặc định `agent_core`), giữ `Orchestrator` cũ làm fallback để 64 test cũ vẫn xanh.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph (`StateGraph` + `MemorySaver`), SQLite, httpx (qua `DeepSeekClient`), pydantic v2, pytest.

## Global Constraints

- LLM chỉ gọi qua `app.llm.client.DeepSeekClient` / `get_llm()` — **KHÔNG** dùng LangChain cho LLM call, **KHÔNG** thêm `langchain-google-genai`.
- Model/endpoint lấy từ `.env`: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL=DeepSeek-V4-Flash`.
- Giữ nguyên contract frontend. Payload `/api/chat` & event `done`: `{reply, stage, question, need, recommendation}` với `recommendation = null` hoặc `{cards, comparison, assumptions, warnings}`.
  - `cards[i]` = `FactCard`: `{title, lines:[{label,value,source}], missing:[str]}`.
  - `comparison` = `ComparisonTable`: `{products:[str], rows:[{label, unit, source, better, cells:[{value, available, is_best}]}]}`.
  - Tiêu đề card đề xuất: `"Vì sao em đề xuất {name}?"`; card chi tiết: `"Thông tin chi tiết: {name}"`.
- SSE events giữ nguyên: `{type:"status"|"delta"|"done"|"error", ...}`.
- Guardrail fail-closed: mọi số trong câu trả lời LLM phải truy được về `cards`; nếu không → thay bằng safe summary + `warnings`.
- Không build lại DB (đã đúng 8.746 dòng, 14 ngành). Không sửa `category_config.py`. Không đổi frontend.
- Cờ `PIPELINE=agent_core|orchestrator` (mặc định `agent_core`). Giữ `Orchestrator` cũ nguyên vẹn.
- Comment tiếng Việt theo phong cách repo. `git add` chỉ file liên quan (KHÔNG add `products.db`, `.env`, `*.xlsx`).

---

## File Structure

**Sửa:**
- `backend/app/config.py` — thêm `pipeline`, `agent_db_path`, `excel_source_path` + resolver.
- `backend/app/agent_core/retriever.py` — `db_path` mặc định lấy từ config.
- `backend/app/agent_core/data_ingestion.py` — path từ config.
- `backend/app/agent_core/agent_engine.py` — **viết lại**: `AgentState`, các node, graph, `AgentCoreEngine`.
- `backend/app/main.py` — chọn engine qua `get_engine()`, gọi `engine.handle(...)` / `engine.reset(...)`.
- `backend/requirements.txt` — thêm `langgraph`.
- `.gitignore` — thêm `products.db`.
- `backend/tests/test_api.py` — override `get_engine` thay vì `get_orchestrator`.

**Thêm (mới):**
- `backend/app/agent_core/__init__.py` — biến agent_core thành package.
- `backend/app/agent_core/intent.py` — `IntentSchema`, `extract_intent`, `extract_intent_fallback`, `has_enough_slots`.
- `backend/app/agent_core/presenters.py` — `product_display_name`, `parse_leading_number`, `load_specs`, `build_reco_card`, `build_detail_card`, `_ALWAYS_MISSING`.
- `backend/app/agent_core/compare.py` — `build_comparison`.
- `backend/app/agent_core/detail.py` — `is_detail_question`, `wants_product_list`, `resolve_product_row`, `answer_detail`.
- `backend/app/agent_core/advisor.py` — `generate_advisor`.
- `backend/app/agent_core/engine.py` — `AgentCoreEngine`, `OrchestratorEngine`, `Engine` protocol.
- `backend/tests/test_agent_intent.py`, `test_agent_presenters.py`, `test_agent_compare.py`, `test_agent_detail.py`, `test_agent_advisor.py`, `test_agent_graph.py`, `test_agent_api.py`, `test_agent_config.py`.

**Giữ nguyên:** `orchestrator.py`, `advice/*`, `nlu/*`, `retrieval/*`, `catalog/*`, `dialogue/*`, toàn bộ `frontend/`.

**Test fixture dùng chung** (mọi task cần DB tạm): tạo trong mỗi test file helper sau (KHÔNG dùng DB 35MB thật):

```python
import sqlite3, json
def make_db(path, rows):
    """rows: list dict có model_code, sku, category, brand, price_clean, gift_promo, key_specs_summary, specs(dict)."""
    conn = sqlite3.connect(path); cur = conn.cursor()
    cur.execute("""CREATE TABLE all_products (id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_code TEXT, sku TEXT, category TEXT, category_table TEXT, brand TEXT,
        price_orig TEXT, price_promo TEXT, price_clean REAL, gift_promo TEXT,
        key_specs_summary TEXT, full_specs_json TEXT)""")
    for r in rows:
        specs = r.get("specs", {})
        summary = "; ".join(f"{k}: {v}" for k, v in list(specs.items())[:8])
        cur.execute("""INSERT INTO all_products
            (model_code, sku, category, category_table, brand, price_orig, price_promo,
             price_clean, gift_promo, key_specs_summary, full_specs_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("model_code",""), r.get("sku",""), r["category"], r.get("category_table",""),
             r.get("brand",""), r.get("price_orig",""), r.get("price_promo",""),
             r.get("price_clean"), r.get("gift_promo",""), summary,
             json.dumps(specs, ensure_ascii=False)))
    conn.commit(); conn.close()
```

---

## Task 1: Config, package scaffolding & data-path fix

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/agent_core/__init__.py`
- Modify: `backend/app/agent_core/retriever.py:6-34` (default db_path resolver; imports)
- Modify: `backend/app/agent_core/data_ingestion.py:12-13` (paths from config)
- Modify: `.gitignore`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_agent_config.py`

**Interfaces:**
- Produces: `app.config.Settings.pipeline: str`, `.agent_db_path: str`, `.excel_source_path: str`; `app.agent_core.retriever.search_products(..., db_path: str | None = None)` (None → config path); `app.agent_core.retriever.get_catalog_metadata(db_path=None)`, `get_schema_summary(db_path=None)`.

- [ ] **Step 1: Add langgraph to requirements**

Modify `backend/requirements.txt`, append after line `pytest-asyncio==0.24.*`:

```
langgraph>=0.2,<0.3
```

- [ ] **Step 2: Install it**

Run: `cd backend && ./.venv/Scripts/pip install "langgraph>=0.2,<0.3"`
Expected: installs langgraph + langchain-core, no errors.

- [ ] **Step 3: Verify import works**

Run: `cd backend && ./.venv/Scripts/python -c "from langgraph.graph import StateGraph, START, END; from langgraph.checkpoint.memory import MemorySaver; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Extend config.py**

Replace the body of `backend/app/config.py` with:

```python
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
# products.db chuẩn hoá về đúng 1 vị trí trong package agent_core, resolve tuyệt đối
# theo vị trí file (không phụ thuộc cwd khi chạy uvicorn / pytest).
_DEFAULT_AGENT_DB = os.path.join(_APP_DIR, "agent_core", "products.db")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost/v1"
    llm_api_key: str = ""
    llm_model: str = "DeepSeek-V4-Flash"
    dataset_path: str = "../Dataset.xlsx"
    catalog_path: str = "./data/catalog.normalized.json"
    enable_embeddings: bool = False
    # Luồng phục vụ: "agent_core" (LangGraph + SQLite) hoặc "orchestrator" (bản cũ).
    pipeline: str = "agent_core"
    # DB SQLite của agent_core; đường dẫn tuyệt đối mặc định, override bằng AGENT_DB_PATH.
    agent_db_path: str = _DEFAULT_AGENT_DB
    # Nguồn Excel để rebuild DB (chỉ dùng khi chạy data_ingestion).
    excel_source_path: str = "../Spec_cate_gia.cleaned.xlsx"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Create the agent_core package marker**

Create `backend/app/agent_core/__init__.py`:

```python
# Package agent_core: luồng agent-graph (LangGraph) phục vụ /api/chat.
```

- [ ] **Step 6: Point retriever default db_path at config**

In `backend/app/agent_core/retriever.py`, replace the top imports (lines 1-4) with:

```python
import sqlite3
import re
import math
from typing import List, Dict, Any, Optional
from app.config import get_settings


def _resolve_db(db_path: Optional[str]) -> str:
    return db_path or get_settings().agent_db_path
```

Then change every function signature default `db_path: str = "products.db"` → `db_path: Optional[str] = None`, and as the FIRST line inside `get_catalog_metadata`, `get_schema_summary`, and `search_products`, add:

```python
    db_path = _resolve_db(db_path)
```

(Inside `search_products`, put it right after the docstring, before `query_lower = query.lower()`.)

- [ ] **Step 7: Point data_ingestion at config**

In `backend/app/agent_core/data_ingestion.py`, replace lines 12-13:

```python
EXCEL_PATH = "d:/Code/Hackathon_V2/Spec_cate_gia.xlsx"
DB_PATH = "d:/Code/Hackathon_V2/products.db"
```

with:

```python
from app.config import get_settings
_S = get_settings()
EXCEL_PATH = _S.excel_source_path
DB_PATH = _S.agent_db_path
```

- [ ] **Step 8: gitignore the DB**

Append to `.gitignore`:

```
products.db
backend/app/agent_core/products.db
```

- [ ] **Step 9: Write config/path test**

Create `backend/tests/test_agent_config.py`:

```python
import os
from app.config import Settings
from app.agent_core.retriever import search_products, get_catalog_metadata
from tests.test_agent_config import make_db  # noqa: F401 (defined below)
```

Wait — define the helper inline instead. Full file:

```python
import sqlite3, json
from app.config import Settings
from app.agent_core.retriever import search_products, get_catalog_metadata


def make_db(path, rows):
    conn = sqlite3.connect(path); cur = conn.cursor()
    cur.execute("""CREATE TABLE all_products (id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_code TEXT, sku TEXT, category TEXT, category_table TEXT, brand TEXT,
        price_orig TEXT, price_promo TEXT, price_clean REAL, gift_promo TEXT,
        key_specs_summary TEXT, full_specs_json TEXT)""")
    for r in rows:
        specs = r.get("specs", {})
        summary = "; ".join(f"{k}: {v}" for k, v in list(specs.items())[:8])
        cur.execute("""INSERT INTO all_products
            (model_code, sku, category, category_table, brand, price_orig, price_promo,
             price_clean, gift_promo, key_specs_summary, full_specs_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("model_code",""), r.get("sku",""), r["category"], r.get("category_table",""),
             r.get("brand",""), r.get("price_orig",""), r.get("price_promo",""),
             r.get("price_clean"), r.get("gift_promo",""), summary,
             json.dumps(specs, ensure_ascii=False)))
    conn.commit(); conn.close()


def test_settings_defaults():
    s = Settings()
    assert s.pipeline in ("agent_core", "orchestrator")
    assert s.agent_db_path.endswith("products.db")


def test_search_uses_explicit_db(tmp_path):
    db = str(tmp_path / "t.db")
    make_db(db, [{"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL1", "price_clean": 12_000_000,
                  "specs": {"Dung tích tổng": "300 lít"}}])
    res = search_products("tủ lạnh", category="Tủ Lạnh", db_path=db)
    assert res["status"] == "exact_match"
    assert res["total_matches_found"] == 1
    assert res["top_3_products"][0]["brand"] == "Toshiba"


def test_metadata_lists_categories(tmp_path):
    db = str(tmp_path / "t.db")
    make_db(db, [{"category": "Máy giặt", "brand": "LG", "price_clean": 9_000_000, "specs": {}}])
    meta = get_catalog_metadata(db)
    assert "Máy giặt" in meta["categories"]
```

- [ ] **Step 10: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_config.py -v`
Expected: 3 PASS.

- [ ] **Step 11: Commit**

```bash
git add backend/requirements.txt backend/app/config.py backend/app/agent_core/__init__.py \
        backend/app/agent_core/retriever.py backend/app/agent_core/data_ingestion.py \
        .gitignore backend/tests/test_agent_config.py
git commit -m "feat(agent_core): config-driven db paths + langgraph dep + package init"
```

---

## Task 2: Intent extraction on DeepSeek (replace Gemini)

**Files:**
- Create: `backend/app/agent_core/intent.py`
- Test: `backend/tests/test_agent_intent.py`

**Interfaces:**
- Consumes: `app.llm.client.LLMClient` (`.complete_json(system, user, schema_hint) -> dict`), `app.agent_core.retriever.get_catalog_metadata`, `get_schema_summary`.
- Produces: `IntentSchema` (pydantic), `extract_intent(query, history, llm, db_path=None) -> dict`, `extract_intent_fallback(query, history, db_path=None) -> dict`, `has_enough_slots(intent: dict) -> bool`. `intent` dict keys: `is_meta_inquiry, category, budget_max, brand, priority_features, needs_clarification, clarification_questions`.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_agent_intent.py`:

```python
import sqlite3, json
from app.llm.client import FakeLLM
from app.agent_core.intent import extract_intent, extract_intent_fallback, has_enough_slots
from tests.test_agent_config import make_db


class BoomLLM:
    def complete_json(self, system, user, schema_hint=""):
        raise RuntimeError("llm down")
    def complete_text(self, s, u): return ""
    def stream_text(self, s, u): yield ""


def _db(tmp_path):
    db = str(tmp_path / "i.db")
    make_db(db, [{"category": "Tủ Lạnh", "brand": "Toshiba", "price_clean": 12_000_000, "specs": {}},
                 {"category": "Máy giặt", "brand": "LG", "price_clean": 9_000_000, "specs": {}}])
    return db


def test_llm_intent_maps_fields(tmp_path):
    db = _db(tmp_path)
    llm = FakeLLM(json_responses=[{"category": "Tủ Lạnh", "budget_max": 20000000,
                                   "brand": "Toshiba", "priority_features": ["tiết kiệm điện"],
                                   "needs_clarification": False, "is_meta_inquiry": False,
                                   "clarification_questions": []}])
    intent = extract_intent("mua tủ lạnh toshiba dưới 20tr tiết kiệm điện", [], llm, db)
    assert intent["category"] == "Tủ Lạnh"
    assert intent["budget_max"] == 20000000
    assert intent["priority_features"] == ["tiết kiệm điện"]
    assert intent["needs_clarification"] is False


def test_llm_error_falls_back(tmp_path):
    db = _db(tmp_path)
    intent = extract_intent("mua tủ lạnh 15 triệu", [], BoomLLM(), db)
    assert intent["category"] == "Tủ Lạnh"
    assert intent["budget_max"] == 15_000_000


def test_fallback_detects_budget_and_brand(tmp_path):
    db = _db(tmp_path)
    intent = extract_intent_fallback("máy giặt LG khoảng 9 triệu", [], db)
    assert intent["category"] == "Máy giặt"
    assert intent["brand"] == "LG"


def test_has_enough_slots():
    assert has_enough_slots({"category": "Tủ Lạnh", "budget_max": 20000000,
                             "priority_features": [], "needs_clarification": False}) is True
    assert has_enough_slots({"category": None, "budget_max": None, "brand": None,
                             "priority_features": [], "needs_clarification": True}) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_intent.py -v`
Expected: FAIL — `ModuleNotFoundError: app.agent_core.intent`.

- [ ] **Step 3: Create intent.py**

Create `backend/app/agent_core/intent.py`. Copy `IntentSchema`, `extract_intent_fallback`, and `has_enough_slots` **verbatim** from the current `agent_engine.py` (lines 21-49 for schema, 51-180 for fallback, 377-391 for has_enough_slots), with two changes: (a) `db_path` default `Optional[str] = None`; (b) rewrite `extract_intent` to use `LLMClient.complete_json`. Full new `extract_intent`:

```python
import json
from typing import List, Dict, Any, Optional
from app.agent_core.retriever import get_catalog_metadata, get_schema_summary

_SCHEMA_HINT = (
    '{"is_meta_inquiry": bool, "category": string|null, "budget_max": number|null, '
    '"brand": string|null, "priority_features": string[], "needs_clarification": bool, '
    '"clarification_questions": string[]}'
)


def extract_intent(query: str, history: Optional[List[Dict[str, str]]] = None,
                   llm=None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Trích ý định qua DeepSeek (LLMClient.complete_json). Lỗi/không có llm -> fallback heuristic."""
    if llm is None:
        return extract_intent_fallback(query, history, db_path)
    try:
        schema_info = get_schema_summary(db_path)
        system = (
            "Bạn là AI phân tích ý định tìm mua điện máy. "
            f"{schema_info}\n"
            "Ánh xạ danh mục theo ngữ nghĩa (laptop/macbook/pc/desktop -> 'Máy tính để bàn'; "
            "ipad/tablet -> 'Máy tính bảng'). Nếu câu hỏi mới đổi loại sản phẩm so với lịch sử, "
            "BẮT BUỘC theo danh mục mới. Nếu Assistant vừa hỏi làm rõ và người dùng đã trả lời mục đích "
            "hoặc ngân sách, đặt needs_clarification=false và đưa mục đích vào priority_features. "
            "Nếu thiếu ngân sách/nhu cầu và chưa từng hỏi, đặt needs_clarification=true kèm 1-2 câu hỏi."
        )
        hist_str = ""
        for m in (history or []):
            role = "User" if m.get("role") == "user" else "Assistant"
            hist_str += f"{role}: {m.get('content')}\n"
        user = f"Lịch sử:\n{hist_str or 'Không có'}\n\nCâu hỏi mới: {query}"
        raw = llm.complete_json(system, user, _SCHEMA_HINT)
        return IntentSchema(**{k: raw[k] for k in IntentSchema.model_fields if k in raw}).model_dump()
    except Exception as e:
        print(f"[Intent LLM Error]: {e}")
        return extract_intent_fallback(query, history, db_path)
```

Keep the copied `extract_intent_fallback` and `has_enough_slots` unchanged except the `db_path` default. **Delete** all LangChain/Gemini imports.

- [ ] **Step 4: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_intent.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_core/intent.py backend/tests/test_agent_intent.py
git commit -m "feat(agent_core): intent extraction via DeepSeek complete_json (drop Gemini)"
```

---

## Task 3: Presenters — display name, spec parsing, fact cards

**Files:**
- Create: `backend/app/agent_core/presenters.py`
- Test: `backend/tests/test_agent_presenters.py`

**Interfaces:**
- Consumes: `app.schemas.FactCard`, `FactLine`; `app.advice.provenance.format_vnd`.
- Produces:
  - `product_display_name(row: dict) -> str`
  - `parse_leading_number(s) -> float | None`
  - `load_specs(row: dict) -> dict[str, str]`
  - `build_reco_card(row: dict, priority_features: list[str]) -> FactCard` (title `"Vì sao em đề xuất {name}?"`)
  - `build_detail_card(row: dict) -> FactCard` (title `"Thông tin chi tiết: {name}"`)
  - `_ALWAYS_MISSING: list[str]`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_agent_presenters.py`:

```python
from app.agent_core.presenters import (product_display_name, parse_leading_number,
                                        load_specs, build_reco_card, build_detail_card)


def _row(**kw):
    base = {"model_code": "TL1", "sku": "S1", "category": "Tủ Lạnh", "brand": "Toshiba",
            "price_clean": 12_400_000, "gift_promo": "", "key_specs_summary": "",
            "full_specs_json": '{"Dung tích tổng": "300 lít", "Điện năng tiêu thụ": "350 kWh/năm"}'}
    base.update(kw); return base


def test_display_name_uses_brand_and_code():
    assert "Toshiba" in product_display_name(_row())
    assert "TL1" in product_display_name(_row())


def test_parse_leading_number():
    assert parse_leading_number("300 lít") == 300.0
    assert parse_leading_number("1,3 kg") == 1.3
    assert parse_leading_number("không") is None


def test_load_specs_parses_json():
    specs = load_specs(_row())
    assert specs["Dung tích tổng"] == "300 lít"


def test_reco_card_has_price_and_missing():
    card = build_reco_card(_row(), ["tiết kiệm điện"])
    assert card.title.startswith("Vì sao em đề xuất")
    labels = [l.label for l in card.lines]
    assert "Giá" in labels and "Thương hiệu" in labels
    assert "tồn kho" in card.missing
    # giá có trong dòng giá
    price_line = next(l for l in card.lines if l.label == "Giá")
    assert "12.400.000" in price_line.value


def test_reco_card_missing_price():
    card = build_reco_card(_row(price_clean=0), [])
    assert "giá" in card.missing


def test_detail_card_lists_all_specs():
    card = build_detail_card(_row())
    labels = [l.label for l in card.lines]
    assert "Dung tích tổng" in labels and "Điện năng tiêu thụ" in labels
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_presenters.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create presenters.py**

```python
from __future__ import annotations
import json
import re
from typing import Any, Dict, List
from app.schemas import FactCard, FactLine
from app.advice.provenance import format_vnd

# Các mục dataset gốc không có -> luôn báo "chưa có dữ liệu".
_ALWAYS_MISSING = ["tồn kho", "đánh giá người dùng (review)", "trả góp"]
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_leading_number(s: Any) -> float | None:
    if s is None:
        return None
    m = _NUM.search(str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def load_specs(row: Dict[str, Any]) -> Dict[str, str]:
    raw = row.get("full_specs_json") or "{}"
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return {str(k): str(v) for k, v in d.items() if str(v).strip() not in ("", "nan", "None")}


def product_display_name(row: Dict[str, Any]) -> str:
    brand = (row.get("brand") or "").strip()
    code = (row.get("model_code") or "").strip() or (row.get("sku") or "").strip()
    if code and not code.upper().startswith("SKU-"):
        return f"{brand} {code}".strip()
    summary = (row.get("key_specs_summary") or "").strip()
    if brand and summary:
        return f"{brand} - {summary[:40]}"
    return brand or summary[:40] or "Sản phẩm"


def _price_value(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("price_clean") or 0)
    except (ValueError, TypeError):
        return 0.0


def build_reco_card(row: Dict[str, Any], priority_features: List[str]) -> FactCard:
    """Card 'vì sao đề xuất': giá + hãng + vài spec liên quan ưu tiên của khách; mọi dòng gắn nguồn."""
    name = product_display_name(row)
    lines: List[FactLine] = []
    missing: List[str] = []
    price = _price_value(row)
    if price > 0:
        lines.append(FactLine(label="Giá", value=format_vnd(int(price)), source="catalog"))
    else:
        missing.append("giá")
    lines.append(FactLine(label="Thương hiệu", value=row.get("brand") or "N/A", source="catalog"))

    specs = load_specs(row)
    prefs_low = [p.lower() for p in (priority_features or [])]
    shown = 0
    for k, v in specs.items():
        relevant = any(p in k.lower() or p in v.lower() for p in prefs_low)
        if relevant and shown < 4:
            lines.append(FactLine(label=k, value=v, source="thông số nhà sản xuất"))
            shown += 1
    if shown == 0:  # không match ưu tiên -> lấy tối đa 3 spec đầu để có dữ kiện gắn nguồn
        for k, v in list(specs.items())[:3]:
            lines.append(FactLine(label=k, value=v, source="thông số nhà sản xuất"))

    if row.get("gift_promo"):
        lines.append(FactLine(label="Khuyến mãi/quà kèm", value=str(row["gift_promo"]),
                              source="khuyến mãi (catalog)"))
    missing.extend(_ALWAYS_MISSING)
    return FactCard(title=f"Vì sao em đề xuất {name}?", lines=lines, missing=missing)


def build_detail_card(row: Dict[str, Any]) -> FactCard:
    """Fact-sheet đầy đủ 1 sản phẩm: giá + TOÀN BỘ spec + quà; mọi dòng gắn nguồn."""
    name = product_display_name(row)
    lines: List[FactLine] = []
    missing: List[str] = []
    price = _price_value(row)
    if price > 0:
        lines.append(FactLine(label="Giá", value=format_vnd(int(price)), source="catalog"))
    else:
        missing.append("giá")
    lines.append(FactLine(label="Thương hiệu", value=row.get("brand") or "N/A", source="catalog"))
    for k, v in load_specs(row).items():
        lines.append(FactLine(label=k, value=v, source="thông số nhà sản xuất"))
    if row.get("gift_promo"):
        lines.append(FactLine(label="Khuyến mãi/quà kèm", value=str(row["gift_promo"]),
                              source="khuyến mãi (catalog)"))
    missing.extend(_ALWAYS_MISSING)
    return FactCard(title=f"Thông tin chi tiết: {name}", lines=lines, missing=missing)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_presenters.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_core/presenters.py backend/tests/test_agent_presenters.py
git commit -m "feat(agent_core): fact-card presenters from SQLite dict-rows"
```

---

## Task 4: Comparison table builder

**Files:**
- Create: `backend/app/agent_core/compare.py`
- Test: `backend/tests/test_agent_compare.py`

**Interfaces:**
- Consumes: `app.schemas.ComparisonTable, ComparisonRow, ComparisonCell`; `app.agent_core.presenters` (`product_display_name`, `load_specs`, `parse_leading_number`); `app.advice.provenance.format_vnd`.
- Produces: `build_comparison(rows: list[dict], priority_features: list[str]) -> ComparisonTable | None` (None nếu < 2 ứng viên).

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_agent_compare.py`:

```python
from app.agent_core.compare import build_comparison


def _row(brand, price, dientnang):
    return {"model_code": brand, "brand": brand, "price_clean": price, "category": "Tủ Lạnh",
            "key_specs_summary": "", "full_specs_json":
            '{"Điện năng tiêu thụ": "%s kWh/năm"}' % dientnang}


def test_none_for_single():
    assert build_comparison([_row("A", 12_000_000, 350)], []) is None


def test_price_row_marks_cheapest_best():
    table = build_comparison([_row("A", 12_000_000, 350), _row("B", 11_000_000, 400)], [])
    price_row = next(r for r in table.rows if r.label == "Giá")
    assert price_row.cells[1].is_best is True   # B rẻ hơn
    assert price_row.cells[0].is_best is False
    assert table.products == ["A", "B"]


def test_brand_row_present():
    table = build_comparison([_row("A", 12_000_000, 350), _row("B", 11_000_000, 400)], [])
    assert any(r.label == "Thương hiệu" for r in table.rows)


def test_energy_row_lower_is_best():
    table = build_comparison([_row("A", 12_000_000, 350), _row("B", 11_000_000, 400)],
                             ["tiết kiệm điện"])
    erow = next((r for r in table.rows if "Điện năng" in r.label), None)
    assert erow is not None
    assert erow.cells[0].is_best is True        # A tiêu thụ 350 < 400


def test_missing_cell_marked_unavailable():
    rows = [{"model_code": "A", "brand": "A", "price_clean": 0, "category": "X",
             "full_specs_json": "{}", "key_specs_summary": ""},
            {"model_code": "B", "brand": "B", "price_clean": 11_000_000, "category": "X",
             "full_specs_json": "{}", "key_specs_summary": ""}]
    table = build_comparison(rows, [])
    price_row = next(r for r in table.rows if r.label == "Giá")
    assert price_row.cells[0].available is False
    assert price_row.cells[0].value == "chưa có dữ liệu"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_compare.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create compare.py**

```python
from __future__ import annotations
from typing import Any, Dict, List
from app.schemas import ComparisonTable, ComparisonRow, ComparisonCell
from app.agent_core.presenters import product_display_name, load_specs, parse_leading_number
from app.advice.provenance import format_vnd

_MISSING = "chưa có dữ liệu"
# Suy hướng "tốt hơn" theo tên field (khớp chuỗi con, không dấu-sensitive tối thiểu).
_LOWER_BETTER = ("điện năng", "tiêu thụ", "độ ồn", "tiêu thụ nước")
_HIGHER_BETTER = ("dung tích", "pin", "bảo hành", "công suất", "tốc độ", "độ sáng", "bộ nhớ")


def _direction(field: str) -> str | None:
    f = field.lower()
    if any(k in f for k in _LOWER_BETTER):
        return "min"
    if any(k in f for k in _HIGHER_BETTER):
        return "max"
    return None


def _best_indices(nums: List[float | None], direction: str) -> set[int]:
    present = [(i, n) for i, n in enumerate(nums) if n is not None]
    if not present:
        return set()
    target = (min if direction == "min" else max)(n for _, n in present)
    return {i for i, n in present if n == target}


def _price_val(row: Dict[str, Any]) -> float | None:
    try:
        v = float(row.get("price_clean") or 0)
    except (ValueError, TypeError):
        return None
    return v if v > 0 else None


def build_comparison(rows: List[Dict[str, Any]], priority_features: List[str]) -> ComparisonTable | None:
    """Bảng so sánh side-by-side, mọi ô lấy trực tiếp từ DB (không qua LLM)."""
    rows = rows[:3]
    if len(rows) < 2:
        return None
    products = [product_display_name(r) for r in rows]
    out_rows: List[ComparisonRow] = []

    # 1) Giá — rẻ hơn tốt hơn
    prices = [_price_val(r) for r in rows]
    price_cells = [ComparisonCell(value=format_vnd(int(p)) if p is not None else _MISSING,
                                  available=p is not None) for p in prices]
    for i in _best_indices(prices, "min"):
        price_cells[i].is_best = True
    out_rows.append(ComparisonRow(label="Giá", unit=None, source="catalog",
                                  cells=price_cells, better="giá thấp hơn tốt hơn"))

    # 2) Spec số dùng chung — ưu tiên field khớp priority_features, rồi field xuất hiện nhiều nhất
    specs_per = [load_specs(r) for r in rows]
    field_count: Dict[str, int] = {}
    for sp in specs_per:
        for k, v in sp.items():
            if parse_leading_number(v) is not None:
                field_count[k] = field_count.get(k, 0) + 1
    prefs_low = [p.lower() for p in (priority_features or [])]

    def rank(field: str):
        pref_hit = any(p in field.lower() for p in prefs_low)
        return (0 if pref_hit else 1, -field_count[field])

    shared = sorted([f for f, c in field_count.items() if c >= 2], key=rank)[:4]
    for field in shared:
        nums = [parse_leading_number(sp.get(field)) for sp in specs_per]
        direction = _direction(field)
        cells = [ComparisonCell(value=(sp.get(field) if sp.get(field) else _MISSING),
                                available=sp.get(field) is not None) for sp in specs_per]
        if direction is not None:
            for i in _best_indices(nums, direction):
                cells[i].is_best = True
        better = ("chỉ số thấp hơn tốt hơn" if direction == "min"
                  else "chỉ số cao hơn tốt hơn" if direction == "max" else None)
        out_rows.append(ComparisonRow(label=field, unit=None, source="thông số nhà sản xuất",
                                       cells=cells, better=better))

    # 3) Thương hiệu (tham khảo)
    out_rows.append(ComparisonRow(label="Thương hiệu", unit=None, source="catalog",
                                  cells=[ComparisonCell(value=r.get("brand") or "N/A") for r in rows],
                                  better=None))
    return ComparisonTable(products=products, rows=out_rows)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_compare.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_core/compare.py backend/tests/test_agent_compare.py
git commit -m "feat(agent_core): side-by-side comparison table from dict-rows"
```

---

## Task 5: Detail deep-dive (resolve + grounded answer, fail-closed)

**Files:**
- Create: `backend/app/agent_core/detail.py`
- Test: `backend/tests/test_agent_detail.py`

**Interfaces:**
- Consumes: `app.nlu.preprocess.strip_accents`; `app.agent_core.presenters` (`product_display_name`, `build_detail_card`); `app.advice.provenance.facts_for_llm`; `app.advice.verify` (`verify_advice`, `is_grounded`); `app.schemas.AdviceResult`; `LLMClient`.
- Produces:
  - `is_detail_question(message: str) -> bool`
  - `wants_product_list(message: str) -> bool`
  - `resolve_product_row(message: str, rows: list[dict]) -> dict | None`
  - `answer_detail(row: dict, question: str, llm) -> tuple[str, FactCard]` (message đã fail-closed, card chi tiết)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_agent_detail.py`:

```python
from app.llm.client import FakeLLM
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)


def _rows():
    return [{"model_code": "A", "brand": "Toshiba", "price_clean": 12_000_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "300 lít"}'},
            {"model_code": "B", "brand": "LG", "price_clean": 11_000_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "250 lít"}'}]


def test_is_detail_question():
    assert is_detail_question("máy này bảo hành thế nào") is True
    assert is_detail_question("mua tủ lạnh 20 triệu") is False


def test_resolve_by_position():
    assert resolve_product_row("cho em xem kỹ máy 2", _rows())["brand"] == "LG"


def test_resolve_by_brand():
    assert resolve_product_row("con toshiba dung tích bao nhiêu", _rows())["brand"] == "Toshiba"


def test_resolve_by_superlative_price():
    assert resolve_product_row("cái rẻ nhất có tốt không", _rows())["brand"] == "LG"


def test_answer_grounded_passthrough():
    llm = FakeLLM(text_responses=["Dạ máy Toshiba dung tích 300 lít ạ."])
    msg, card = answer_detail(_rows()[0], "dung tích bao nhiêu", llm)
    assert "300" in msg
    assert card.title.startswith("Thông tin chi tiết")


def test_answer_fail_closed_on_hallucination():
    # LLM bịa số 999 không có trong fact-sheet -> phải bị thay bằng safe summary.
    llm = FakeLLM(text_responses=["Máy này chỉ 999 lít và giá 5000000đ."])
    msg, card = answer_detail(_rows()[0], "thông số", llm)
    assert "999" not in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_detail.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create detail.py**

```python
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from app.schemas import AdviceResult, FactCard
from app.nlu.preprocess import strip_accents
from app.agent_core.presenters import product_display_name, build_detail_card
from app.advice.provenance import facts_for_llm
from app.advice.verify import verify_advice, is_grounded

_POSITION: Dict[str, int] = {
    "dau tien": 0, "thu nhat": 0, "may 1": 0, "cai 1": 0, "so 1": 0, "thu 1": 0, "mau 1": 0,
    "thu hai": 1, "may 2": 1, "cai 2": 1, "so 2": 1, "thu 2": 1, "mau 2": 1, "o giua": 1,
    "cuoi cung": 2, "thu ba": 2, "may 3": 2, "cai 3": 2, "so 3": 2, "thu 3": 2, "mau 3": 2, "cuoi": 2,
}
_DETAIL_KW = ["chi tiet", "ky hon", "ky ve", "cu the", "thong so", "bao nhieu", "the nao",
              "co gi", "noi them", "noi ro", "bao hanh", "kich thuoc", "can nang", "khoi luong",
              "mau sac", "cong nghe", "tinh nang", "dung tich", "pin", "man hinh", "chi so",
              "co tot khong", "danh gia", "tim hieu", "xem them", "ra sao", "nhu the nao",
              "kieu dang", "xuat xu", "san xuat", "cong suat", "trong luong",
              "diem manh", "uu diem", "nhuoc diem"]
_LIST_KW = ["may khac", "san pham khac", "cai khac", "lua chon khac", "danh sach",
            "may nao khac", "con gi khac", "quay lai", "xem lai danh sach", "so sanh lai"]

_DETAIL_SYSTEM = (
    "Bạn là nhân viên tư vấn điện máy thân thiện, nói tiếng Việt bình dân. Khách đang hỏi kỹ về MỘT "
    "sản phẩm. Bạn CHỈ được dùng dữ kiện trong phần FACTS; TUYỆT ĐỐI không bịa thông số, giá, khuyến mãi, "
    "tồn kho, đánh giá. Nếu thông tin khách hỏi không có trong FACTS, nói thẳng 'dạ em chưa có dữ liệu về ... ạ'. "
    "Trả lời thẳng vào câu hỏi, ngắn gọn, thân thiện."
)


def is_detail_question(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(kw in flat for kw in _DETAIL_KW)


def wants_product_list(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(k in flat for k in _LIST_KW)


def resolve_product_row(message: str, rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not rows:
        return None
    flat = strip_accents(message.lower())
    for key, idx in _POSITION.items():
        if key in flat and idx < len(rows):
            return rows[idx]
    for r in rows:
        b = strip_accents((r.get("brand") or "").lower()).strip()
        if len(b) >= 2 and b in flat:
            return r
    priced = [r for r in rows if float(r.get("price_clean") or 0) > 0]
    if priced and ("re nhat" in flat or "gia thap nhat" in flat or "gia tot nhat" in flat):
        return min(priced, key=lambda r: float(r["price_clean"]))
    if priced and ("dat nhat" in flat or "cao cap nhat" in flat or "xin nhat" in flat):
        return max(priced, key=lambda r: float(r["price_clean"]))
    return None


def _safe_summary(row: Dict[str, Any], card: FactCard) -> str:
    keep = [l for l in card.lines if l.label in ("Giá", "Thương hiệu")]
    head = "; ".join(f"{l.label} {l.value}" for l in keep) if keep else "thông tin cơ bản"
    return (f"Dạ về {product_display_name(row)}: {head}. "
            "Anh/chị muốn biết thêm thông số cụ thể nào ạ?")


def answer_detail(row: Dict[str, Any], question: str, llm) -> Tuple[str, FactCard]:
    """Trả lời sâu 1 sản phẩm, grounded trong fact-sheet; fail-closed nếu LLM bịa số."""
    card = build_detail_card(row)
    facts = facts_for_llm([card])
    user = (f'Khách hỏi về "{product_display_name(row)}": "{question}"\n\n'
            f"FACTS:\n{facts}\n\nTrả lời khách theo đúng quy tắc.")
    try:
        message = llm.complete_text(_DETAIL_SYSTEM, user)
    except Exception:
        message = ""
    result = verify_advice(AdviceResult(message=message or "", cards=[card], assumptions=[], warnings=[]))
    if not message or not is_grounded(result):
        return _safe_summary(row, card), card
    return message, card
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_detail.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_core/detail.py backend/tests/test_agent_detail.py
git commit -m "feat(agent_core): product deep-dive with fail-closed grounding"
```

---

## Task 6: Advisor generation (top-3 + trade-off, blocking + streaming)

**Files:**
- Create: `backend/app/agent_core/advisor.py`
- Test: `backend/tests/test_agent_advisor.py`

**Interfaces:**
- Consumes: `app.agent_core.presenters.build_reco_card`, `product_display_name`; `app.advice.provenance.facts_for_llm, format_vnd`; `app.advice.verify` (`allowed_numbers`, `line_is_grounded`, `verify_advice`, `is_grounded`); `app.agent_core.retriever.get_catalog_metadata`; `app.schemas.AdviceResult, FactCard`; `LLMClient`.
- Produces:
  - `build_cards(rows: list[dict], priority_features: list[str]) -> list[FactCard]`
  - `generate_advisor(query, intent, rows, status, llm, cards, on_delta=None) -> tuple[str, bool, list[str]]` returns `(message, streamed, warnings)`.
  - `deterministic_message(intent, status, db_path=None) -> str | None` (meta_inquiry / no_products / clarify copy).

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_agent_advisor.py`:

```python
from app.llm.client import FakeLLM
from app.agent_core.advisor import build_cards, generate_advisor


def _rows():
    return [{"model_code": "A", "brand": "Toshiba", "price_clean": 12_400_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "300 lít"}'},
            {"model_code": "B", "brand": "LG", "price_clean": 11_000_000, "category": "Tủ Lạnh",
             "key_specs_summary": "", "full_specs_json": '{"Dung tích tổng": "250 lít"}'}]


def test_build_cards_titles():
    cards = build_cards(_rows(), ["tiết kiệm điện"])
    assert cards[0].title.startswith("Vì sao em đề xuất")
    assert len(cards) == 2


def test_generate_blocking_grounded():
    llm = FakeLLM(text_responses=["Máy Toshiba giá 12.400.000đ, dung tích 300 lít, rất phù hợp."])
    cards = build_cards(_rows(), [])
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"priority_features": []},
                                               _rows(), "exact_match", llm, cards)
    assert "12.400.000" in msg
    assert streamed is False
    assert warnings == []


def test_generate_fail_closed_when_ungrounded():
    llm = FakeLLM(text_responses=["Giá chỉ 5.555.555đ thôi ạ."])   # số không có trong cards
    cards = build_cards(_rows(), [])
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"priority_features": []},
                                               _rows(), "exact_match", llm, cards)
    assert "5.555.555" not in msg    # đã fail-closed thay bằng safe summary
    assert warnings and warnings[0].startswith("Số chưa truy được nguồn")


def test_streaming_emits_verified_lines():
    llm = FakeLLM(text_responses=["Máy Toshiba giá 12.400.000đ.\nRất bền và đẹp.\n"])
    cards = build_cards(_rows(), [])
    got = []
    msg, streamed, warnings = generate_advisor("tủ lạnh", {"priority_features": []},
                                               _rows(), "exact_match", llm, cards, on_delta=got.append)
    assert streamed is True
    assert "".join(got).strip().startswith("Máy Toshiba")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_advisor.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create advisor.py**

```python
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple
from app.schemas import AdviceResult, FactCard
from app.agent_core.presenters import build_reco_card, product_display_name
from app.agent_core.retriever import get_catalog_metadata
from app.advice.provenance import facts_for_llm, format_vnd
from app.advice.verify import allowed_numbers, line_is_grounded, verify_advice, is_grounded

_SYSTEM = (
    "Bạn là chuyên gia tư vấn điện máy. NGUYÊN TẮC:\n"
    "1. CHỈ tư vấn dựa trên FACTS cung cấp; tuyệt đối không bịa thông số/giá ngoài FACTS.\n"
    "2. Trình bày thông số bằng lợi ích thực tế (Inverter -> tiết kiệm điện, RAM lớn -> đa nhiệm mượt).\n"
    "3. Phân tích đánh đổi (trade-off) rõ giữa các lựa chọn để khách dễ quyết.\n"
    "4. Nếu trạng thái là budget_fallback: nói rõ không có sản phẩm trong ngân sách đó, rồi giới thiệu "
    "các mẫu giá gần nhất và ưu điểm để khách cân nhắc tăng ngân sách.\n"
    "5. Giọng chuyên nghiệp, mạch lạc, súc tích, đúng ngữ pháp."
)


def build_cards(rows: List[Dict[str, Any]], priority_features: List[str]) -> List[FactCard]:
    return [build_reco_card(r, priority_features) for r in rows]


def deterministic_message(intent: Dict[str, Any], status: str, db_path: Optional[str] = None) -> Optional[str]:
    """Copy tất định cho meta_inquiry / no_products_found; None nếu cần LLM."""
    if status == "meta_inquiry":
        meta = get_catalog_metadata(db_path)
        cats = ", ".join(f"**{c}**" for c in meta["categories"])
        return (f"Chào bạn, hệ thống hiện có **{len(meta['categories'])} danh mục** chính:\n\n{cats}\n\n"
                "Bạn quan tâm danh mục nào, ngân sách và tính năng ra sao ạ?")
    if status == "no_products_found":
        cat = f" thuộc danh mục **{intent['category']}**" if intent.get("category") else ""
        bud = (f" trong mức dưới **{format_vnd(int(intent['budget_max']))}**"
               if intent.get("budget_max") else "")
        return (f"Rất tiếc, hiện chưa có sản phẩm nào{cat}{bud} khớp yêu cầu của bạn.\n\n"
                "Bạn thử nới ngân sách, đổi thương hiệu hoặc danh mục khác nhé!")
    return None


def _context(rows: List[Dict[str, Any]], cards: List[FactCard]) -> str:
    return facts_for_llm(cards)


def generate_advisor(query: str, intent: Dict[str, Any], rows: List[Dict[str, Any]],
                     status: str, llm, cards: List[FactCard],
                     on_delta: Optional[Callable[[str], None]] = None) -> Tuple[str, bool, List[str]]:
    """Sinh tư vấn top-3 + trade-off. Trả (message, streamed, warnings). Fail-closed nếu bịa số."""
    det = deterministic_message(intent, status, None)
    if det is not None:
        return det, False, []
    if not rows:
        return ("Rất tiếc, hiện chưa có sản phẩm phù hợp. Bạn thử nới ngân sách hoặc đổi tiêu chí nhé!",
                False, [])

    facts = _context(rows, cards)
    user = (f"Trạng thái tìm kiếm: {status}\nFACTS (chỉ dùng dữ kiện này):\n{facts}\n\n"
            f"Nhu cầu khách: {query}\n\nHãy tư vấn top sản phẩm kèm phân tích trade-off.")

    # Streaming: phát từng dòng đã verify (line-level fail-closed).
    if on_delta is not None:
        allowed = allowed_numbers(cards)
        parts: List[str] = []
        buf = ""
        emitting = True

        def push(line: str) -> None:
            nonlocal emitting
            if emitting and line_is_grounded(line, allowed):
                on_delta(line)
            else:
                emitting = False

        try:
            for token in llm.stream_text(_SYSTEM, user):
                parts.append(token)
                buf += token
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    push(line + "\n")
            if buf:
                push(buf)
        except Exception:
            return _blocking(llm, user, cards)
        result = verify_advice(AdviceResult(message="".join(parts), cards=cards, assumptions=[], warnings=[]))
        if not is_grounded(result):
            return _safe_summary(cards), False, list(result.warnings)
        return result.message, emitting, []

    return _blocking(llm, user, cards)


def _blocking(llm, user: str, cards: List[FactCard]) -> Tuple[str, bool, List[str]]:
    try:
        message = llm.complete_text(_SYSTEM, user)
    except Exception:
        return _safe_summary(cards), False, []
    result = verify_advice(AdviceResult(message=message, cards=cards, assumptions=[], warnings=[]))
    if not is_grounded(result):
        return _safe_summary(cards), False, list(result.warnings)
    return result.message, False, []


def _safe_summary(cards: List[FactCard]) -> str:
    lines = ["Dạ em gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        title = c.title.replace("Vì sao em đề xuất ", "").rstrip("?")
        lines.append(f"{i}. {title} — giá {price}.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_advisor.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_core/advisor.py backend/tests/test_agent_advisor.py
git commit -m "feat(agent_core): advisor generation with streaming + fail-closed verify"
```

---

## Task 7: LangGraph graph, nodes & AgentCoreEngine

**Files:**
- Modify (rewrite): `backend/app/agent_core/agent_engine.py`
- Create: `backend/app/agent_core/engine.py`
- Test: `backend/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: everything from Tasks 2-6 + `app.agent_core.retriever.search_products` + `langgraph`.
- Produces:
  - `agent_engine.AgentState` (TypedDict), nodes `intent_node`, `clarify_node`, `detail_node`, `retrieval_node`, `advisor_node`, `compare_node`, `verify_node`, `router_edge`, `get_compiled_graph()`.
  - `engine.AgentCoreEngine(llm=None, db_path=None)` with `.handle(session_id, message, on_status=None, on_delta=None) -> dict` and `.reset(session_id)`.
  - `engine.OrchestratorEngine(store, llm)` with same `.handle/.reset` for the old pipeline.
  - `engine.Engine` Protocol.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_agent_graph.py`:

```python
from app.llm.client import FakeLLM
from app.agent_core.engine import AgentCoreEngine
from tests.test_agent_config import make_db


def _db(tmp_path):
    db = str(tmp_path / "g.db")
    make_db(db, [
        {"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL1", "price_clean": 12_400_000,
         "specs": {"Dung tích tổng": "300 lít", "Điện năng tiêu thụ": "350 kWh/năm"}},
        {"category": "Tủ Lạnh", "brand": "LG", "model_code": "TL2", "price_clean": 11_000_000,
         "specs": {"Dung tích tổng": "250 lít", "Điện năng tiêu thụ": "300 kWh/năm"}},
    ])
    return db


def _reco_llm():
    # intent (json) rồi advisor (text)
    return FakeLLM(
        json_responses=[{"category": "Tủ Lạnh", "budget_max": 20000000, "priority_features": ["tiết kiệm điện"],
                         "needs_clarification": False, "is_meta_inquiry": False,
                         "clarification_questions": [], "brand": None}],
        text_responses=["Máy Toshiba giá 12.400.000đ và LG giá 11.000.000đ, cả hai tiết kiệm điện tốt."])


def test_recommend_turn_shape(tmp_path):
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=_db(tmp_path))
    out = eng.handle("s1", "mua tủ lạnh dưới 20tr tiết kiệm điện")
    assert out["stage"] == "recommended"
    assert out["recommendation"] is not None
    assert len(out["recommendation"]["cards"]) >= 2
    assert out["recommendation"]["comparison"] is not None
    assert "12.400.000" in out["reply"]


def test_clarify_turn(tmp_path):
    llm = FakeLLM(json_responses=[{"category": None, "budget_max": None, "brand": None,
                                   "priority_features": [], "needs_clarification": True,
                                   "is_meta_inquiry": False,
                                   "clarification_questions": ["Bạn cần nhóm sản phẩm nào ạ?"]}])
    eng = AgentCoreEngine(llm=llm, db_path=_db(tmp_path))
    out = eng.handle("s2", "tư vấn giúp em")
    assert out["stage"] == "collecting"
    assert out["recommendation"] is None
    assert "?" in out["reply"]


def test_detail_followup_uses_memory(tmp_path):
    db = _db(tmp_path)
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=db)
    eng.handle("s3", "mua tủ lạnh dưới 20tr tiết kiệm điện")   # tạo last_products
    # lượt sau: hỏi chi tiết máy 1 — intent llm cạn text, dùng detail path (không cần advisor)
    eng.llm = FakeLLM(json_responses=[{"category": "Tủ Lạnh", "needs_clarification": False,
                                       "is_meta_inquiry": False, "priority_features": [],
                                       "clarification_questions": [], "brand": None, "budget_max": None}],
                      text_responses=["Dạ máy Toshiba dung tích 300 lít ạ."])
    out = eng.handle("s3", "máy 1 dung tích bao nhiêu")
    assert "300" in out["reply"]
    assert out["recommendation"]["cards"][0]["title"].startswith("Thông tin chi tiết")


def test_reset_clears_memory(tmp_path):
    eng = AgentCoreEngine(llm=_reco_llm(), db_path=_db(tmp_path))
    eng.handle("s4", "mua tủ lạnh dưới 20tr tiết kiệm điện")
    eng.reset("s4")
    # sau reset, hỏi chi tiết mà không có last_products -> không có card chi tiết
    eng.llm = FakeLLM(json_responses=[{"category": None, "needs_clarification": True,
                                       "is_meta_inquiry": False, "priority_features": [],
                                       "clarification_questions": ["Bạn cần gì ạ?"],
                                       "brand": None, "budget_max": None}])
    out = eng.handle("s4", "máy 1 thế nào")
    assert out["stage"] == "collecting"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_graph.py -v`
Expected: FAIL — `app.agent_core.engine` not found.

- [ ] **Step 3: Rewrite agent_engine.py (graph + nodes)**

Replace the ENTIRE contents of `backend/app/agent_core/agent_engine.py` with:

```python
from __future__ import annotations
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agent_core.intent import extract_intent, has_enough_slots
from app.agent_core.retriever import search_products
from app.agent_core.advisor import build_cards, generate_advisor
from app.agent_core.compare import build_comparison
from app.agent_core.detail import (is_detail_question, wants_product_list,
                                    resolve_product_row, answer_detail)
from app.agent_core.presenters import product_display_name
from app.nlu.preprocess import strip_accents


class AgentState(TypedDict, total=False):
    query: str
    history: List[Dict[str, str]]
    intent: Dict[str, Any]
    retrieval: Dict[str, Any]
    last_products: List[Dict[str, Any]]
    focused_sku: Optional[str]
    stage: str
    question: Optional[str]
    response: str
    cards: List[Dict[str, Any]]
    comparison: Optional[Dict[str, Any]]
    assumptions: List[str]
    warnings: List[str]
    next_action: str
    # runtime-only (không checkpoint quan trọng): callback + cấu hình
    _llm: Any
    _db_path: Optional[str]
    _on_status: Any
    _on_delta: Any


def _notify(state: AgentState, text: str) -> None:
    cb = state.get("_on_status")
    if cb:
        cb(text)


def _sku(row: Dict[str, Any]) -> str:
    return str(row.get("model_code") or row.get("sku") or product_display_name(row))


def intent_node(state: AgentState) -> AgentState:
    _notify(state, "Em đang đọc yêu cầu của anh/chị…")
    query = state.get("query", "")
    history = list(state.get("history", []))
    intent = extract_intent(query, history, state.get("_llm"), state.get("_db_path"))
    history = history + [{"role": "user", "content": query}]
    return {"intent": intent, "history": history}


def _is_detail_followup(state: AgentState) -> bool:
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    if not last:
        return False
    intent = state.get("intent", {})
    # Đổi ngành hàng -> tìm mới, không phải hỏi chi tiết.
    cat = intent.get("category")
    if cat and last and last[0].get("category") and cat != last[0].get("category"):
        return False
    if resolve_product_row(query, last) is not None:
        return True
    if state.get("focused_sku") and is_detail_question(query) and not wants_product_list(query):
        return True
    return False


def router_edge(state: AgentState) -> str:
    intent = state.get("intent", {})
    if _is_detail_followup(state):
        return "detail"
    if intent.get("needs_clarification") and not has_enough_slots(intent):
        return "clarify"
    return "retrieve"


def clarify_node(state: AgentState) -> AgentState:
    intent = state.get("intent", {})
    qs = intent.get("clarification_questions") or ["Bạn cho em thêm thông tin về ngân sách và nhu cầu nhé."]
    questions = "\n".join(f"- {q}" for q in qs)
    cat = intent.get("category") or "sản phẩm"
    text = (f"Chào bạn, để tư vấn chuẩn dòng **{cat}** theo đúng nhu cầu, bạn chia sẻ thêm giúp em:\n\n{questions}")
    history = state.get("history", []) + [{"role": "assistant", "content": text}]
    return {"response": text, "question": qs[0] if qs else None, "stage": "collecting",
            "cards": [], "comparison": None, "assumptions": [], "warnings": [], "history": history}


def detail_node(state: AgentState) -> AgentState:
    _notify(state, "Em đang tra cứu chi tiết sản phẩm…")
    query = state.get("query", "")
    last = state.get("last_products", []) or []
    row = resolve_product_row(query, last)
    if row is None and state.get("focused_sku"):
        row = next((r for r in last if _sku(r) == state["focused_sku"]), None)
    if row is None:
        row = last[0]
    message, card = answer_detail(row, query, state.get("_llm"))
    history = state.get("history", []) + [{"role": "assistant", "content": message}]
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [card.model_dump()], "comparison": None, "assumptions": [], "warnings": [],
            "focused_sku": _sku(row), "history": history}


def retrieval_node(state: AgentState) -> AgentState:
    _notify(state, "Em đang tìm máy phù hợp trong catalog…")
    intent = state.get("intent", {})
    res = search_products(
        query=state.get("query", ""),
        category=intent.get("category"),
        max_price=intent.get("budget_max"),
        brand=intent.get("brand"),
        priority_features=intent.get("priority_features"),
        top_k=5,
        db_path=state.get("_db_path"),
        is_meta_inquiry=intent.get("is_meta_inquiry", False),
    )
    return {"retrieval": res, "last_products": res.get("top_3_products", []), "focused_sku": None}


def advisor_node(state: AgentState) -> AgentState:
    _notify(state, "Em đang soạn lời tư vấn…")
    intent = state.get("intent", {})
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    status = res.get("status", "exact_match")
    cards = build_cards(rows, intent.get("priority_features", []))
    message, streamed, warnings = generate_advisor(
        state.get("query", ""), intent, rows, status, state.get("_llm"), cards,
        on_delta=state.get("_on_delta"))
    return {"response": message, "stage": "recommended", "question": None,
            "cards": [c.model_dump() for c in cards], "warnings": warnings,
            "assumptions": [], "_streamed": streamed}


def compare_node(state: AgentState) -> AgentState:
    res = state.get("retrieval", {})
    rows = res.get("top_3_products", [])
    intent = state.get("intent", {})
    table = build_comparison(rows, intent.get("priority_features", []))
    return {"comparison": table.model_dump() if table else None}


def verify_node(state: AgentState) -> AgentState:
    # Guardrail đã áp trong generate_advisor (fail-closed). Node này là điểm mở rộng
    # + đảm bảo history có câu trả lời cuối cùng.
    history = state.get("history", []) + [{"role": "assistant", "content": state.get("response", "")}]
    return {"history": history}


_COMPILED = None


def get_compiled_graph():
    global _COMPILED
    if _COMPILED is None:
        wf = StateGraph(AgentState)
        wf.add_node("intent_node", intent_node)
        wf.add_node("clarify_node", clarify_node)
        wf.add_node("detail_node", detail_node)
        wf.add_node("retrieval_node", retrieval_node)
        wf.add_node("advisor_node", advisor_node)
        wf.add_node("compare_node", compare_node)
        wf.add_node("verify_node", verify_node)
        wf.add_edge(START, "intent_node")
        wf.add_conditional_edges("intent_node", router_edge,
                                 {"clarify": "clarify_node", "detail": "detail_node",
                                  "retrieve": "retrieval_node"})
        wf.add_edge("clarify_node", END)
        wf.add_edge("detail_node", END)
        wf.add_edge("retrieval_node", "advisor_node")
        wf.add_edge("advisor_node", "compare_node")
        wf.add_edge("compare_node", "verify_node")
        wf.add_edge("verify_node", END)
        _COMPILED = wf.compile(checkpointer=MemorySaver())
    return _COMPILED
```

Note the `_streamed` extra channel: add `_streamed: bool` to `AgentState` TypedDict (after `_on_delta`).

- [ ] **Step 4: Create engine.py**

```python
from __future__ import annotations
from typing import Any, Callable, Dict, Optional, Protocol
from app.config import get_settings
from app.llm.client import get_llm
from app.agent_core.agent_engine import get_compiled_graph


class Engine(Protocol):
    def handle(self, session_id: str, message: str,
               on_status: Optional[Callable[[str], None]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]: ...
    def reset(self, session_id: str) -> None: ...


def _need_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    # Map intent -> shape NeedProfile để giữ field cho contract (frontend không render need).
    return {"category": intent.get("category"), "budget_min": None,
            "budget_max": intent.get("budget_max"), "constraints": {},
            "prefs": intent.get("priority_features", []), "demographics": {},
            "known": [], "assumptions": []}


class AgentCoreEngine:
    """Phục vụ 1 lượt qua LangGraph. Memory qua MemorySaver (thread_id có version cho reset)."""
    def __init__(self, llm: Any = None, db_path: Optional[str] = None):
        self.llm = llm if llm is not None else get_llm()
        self.db_path = db_path or get_settings().agent_db_path
        self.graph = get_compiled_graph()
        self._epoch: Dict[str, int] = {}

    def _thread(self, sid: str) -> str:
        return f"{sid}:{self._epoch.get(sid, 0)}"

    def handle(self, session_id: str, message: str,
               on_status: Optional[Callable[[str], None]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        config = {"configurable": {"thread_id": self._thread(session_id)}}
        inputs = {"query": message, "_llm": self.llm, "_db_path": self.db_path,
                  "_on_status": on_status, "_on_delta": on_delta}
        result = self.graph.invoke(inputs, config=config)
        intent = result.get("intent", {})
        stage = result.get("stage", "collecting")
        recommendation = None
        if stage == "recommended":
            recommendation = {"cards": result.get("cards", []),
                              "assumptions": result.get("assumptions", []),
                              "warnings": result.get("warnings", []),
                              "comparison": result.get("comparison")}
        return {"reply": result.get("response", ""), "stage": stage,
                "question": result.get("question"), "need": _need_from_intent(intent),
                "recommendation": recommendation}

    def reset(self, session_id: str) -> None:
        self._epoch[session_id] = self._epoch.get(session_id, 0) + 1


class OrchestratorEngine:
    """Adapter bọc Orchestrator cũ về cùng interface (dùng khi PIPELINE=orchestrator / test cũ)."""
    def __init__(self, store, llm):
        from app.orchestrator import Orchestrator
        from app.session import SESSIONS
        self.orch = Orchestrator(store, llm)
        self.sessions = SESSIONS

    def handle(self, session_id: str, message: str, on_status=None, on_delta=None) -> Dict[str, Any]:
        from app.main import _turn_payload
        state = self.sessions.get(session_id)
        state, result = self.orch.handle_turn(state, message, on_status=on_status, on_delta=on_delta)
        self.sessions.set(session_id, state)
        return _turn_payload(result)

    def reset(self, session_id: str) -> None:
        self.sessions.reset(session_id)
```

- [ ] **Step 5: Run tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_graph.py -v`
Expected: 4 PASS. If `test_detail_followup_uses_memory` fails on routing, verify `_is_detail_followup` sees `last_products` from the checkpoint (MemorySaver persists across `handle` calls with same thread).

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_core/agent_engine.py backend/app/agent_core/engine.py backend/tests/test_agent_graph.py
git commit -m "feat(agent_core): LangGraph StateGraph + MemorySaver engine with payload mapping"
```

---

## Task 8: Wire main.py (engine selection, streaming, reset)

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api.py` (override `get_engine`)
- Test: `backend/tests/test_agent_api.py`

**Interfaces:**
- Consumes: `app.agent_core.engine.AgentCoreEngine`, `OrchestratorEngine`; `app.config.get_settings`.
- Produces: `app.main.get_engine() -> Engine`; endpoints unchanged in shape.

- [ ] **Step 1: Write failing API test**

Create `backend/tests/test_agent_api.py`:

```python
from fastapi.testclient import TestClient
from app.main import app, get_engine
from app.agent_core.engine import AgentCoreEngine
from app.llm.client import FakeLLM
from tests.test_agent_config import make_db

import os, tempfile


def _engine_factory():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "api.db")
    make_db(db, [
        {"category": "Tủ Lạnh", "brand": "Toshiba", "model_code": "TL1", "price_clean": 12_400_000,
         "specs": {"Dung tích tổng": "300 lít"}},
        {"category": "Tủ Lạnh", "brand": "LG", "model_code": "TL2", "price_clean": 11_000_000,
         "specs": {"Dung tích tổng": "250 lít"}},
    ])
    llm = FakeLLM(
        json_responses=[{"category": "Tủ Lạnh", "budget_max": 20000000, "priority_features": [],
                         "needs_clarification": False, "is_meta_inquiry": False,
                         "clarification_questions": [], "brand": None}] * 5,
        text_responses=["Máy Toshiba giá 12.400.000đ, LG giá 11.000.000đ."] * 5)
    return AgentCoreEngine(llm=llm, db_path=db)


def test_chat_agent_core_shape():
    app.dependency_overrides[get_engine] = _engine_factory
    try:
        client = TestClient(app)
        r = client.post("/api/chat", json={"session_id": "a1", "message": "mua tủ lạnh 20tr"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"reply", "stage", "question", "need", "recommendation"}
        assert body["recommendation"]["comparison"] is not None
    finally:
        app.dependency_overrides.clear()


def test_reset_endpoint():
    app.dependency_overrides[get_engine] = _engine_factory
    try:
        client = TestClient(app)
        client.post("/api/chat", json={"session_id": "a2", "message": "mua tủ lạnh 20tr"})
        r = client.post("/api/reset", json={"session_id": "a2"})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_stream_yields_done():
    app.dependency_overrides[get_engine] = _engine_factory
    try:
        client = TestClient(app)
        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "a3", "message": "mua tủ lạnh 20tr"}) as r:
            body = "".join(chunk for chunk in r.iter_text())
        assert '"type": "done"' in body
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_api.py -v`
Expected: FAIL — `cannot import name 'get_engine'`.

- [ ] **Step 3: Rewrite main.py to use engines**

Replace `backend/app/main.py` with:

```python
from __future__ import annotations
import json
import time
from queue import Queue
from threading import Thread
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.orchestrator import TurnResult
from app.catalog.loader import get_store
from app.llm.client import get_llm
from app.config import get_settings
from app.agent_core.engine import AgentCoreEngine, OrchestratorEngine, Engine

app = FastAPI(title="Trợ lý AI Điện Máy Xanh")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])

STREAM_CHUNK_CHARS = 12
STREAM_CHUNK_DELAY_S = 0.02
LIVE_SLICE_CHARS = 4
LIVE_SLICE_DELAY_S = 0.02

_AGENT_ENGINE: AgentCoreEngine | None = None


class ChatIn(BaseModel):
    session_id: str
    message: str


class ResetIn(BaseModel):
    session_id: str


def get_engine() -> Engine:
    """Chọn engine theo cờ PIPELINE. agent_core dùng singleton để giữ MemorySaver + epoch."""
    global _AGENT_ENGINE
    if get_settings().pipeline == "orchestrator":
        return OrchestratorEngine(get_store(), get_llm())
    if _AGENT_ENGINE is None:
        _AGENT_ENGINE = AgentCoreEngine()
    return _AGENT_ENGINE


def _turn_payload(result: TurnResult) -> dict:
    recommendation = None
    if result.advice is not None:
        recommendation = {
            "cards": [c.model_dump() for c in result.advice.cards],
            "assumptions": result.advice.assumptions,
            "warnings": result.advice.warnings,
            "comparison": (result.advice.comparison.model_dump()
                           if result.advice.comparison else None),
        }
    return {"reply": result.reply, "stage": result.stage,
            "question": result.question, "need": result.need.model_dump(),
            "recommendation": recommendation}


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@app.get("/api/health")
def health():
    try:
        n = len(get_store().all())
    except Exception:
        n = 0
    return {"status": "ok", "products": n}


@app.post("/api/chat")
def chat(body: ChatIn, engine: Engine = Depends(get_engine)):
    return engine.handle(body.session_id, body.message)


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, engine: Engine = Depends(get_engine)):
    def event_gen():
        q: Queue = Queue()

        def run_turn():
            try:
                payload = engine.handle(
                    body.session_id, body.message,
                    on_status=lambda t: q.put(("status", t)),
                    on_delta=lambda t: q.put(("delta", t)))
                q.put(("result", payload))
            except Exception:
                q.put(("error", None))

        Thread(target=run_turn, daemon=True).start()
        live = False
        while True:
            kind, val = q.get()
            if kind == "status":
                yield _sse({"type": "status", "text": val})
            elif kind == "delta":
                live = True
                for i in range(0, len(val), LIVE_SLICE_CHARS):
                    yield _sse({"type": "delta", "text": val[i:i + LIVE_SLICE_CHARS]})
                    time.sleep(LIVE_SLICE_DELAY_S)
            elif kind == "error":
                yield _sse({"type": "error"})
                return
            else:
                if not live:
                    reply = val["reply"]
                    for i in range(0, len(reply), STREAM_CHUNK_CHARS):
                        yield _sse({"type": "delta", "text": reply[i:i + STREAM_CHUNK_CHARS]})
                        time.sleep(STREAM_CHUNK_DELAY_S)
                yield _sse({"type": "done", **val})
                return

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
def reset(body: ResetIn, engine: Engine = Depends(get_engine)):
    engine.reset(body.session_id)
    return {"status": "reset"}
```

Note: `OrchestratorEngine.handle` imports `_turn_payload` from `app.main` — that's this module, so it resolves at call time (no circular import at module load).

- [ ] **Step 4: Update test_api.py to override get_engine**

In `backend/tests/test_api.py`: change import line `from app.main import app, get_orchestrator` → `from app.main import app, get_engine`. Replace `_fake_orch` with a factory returning an `OrchestratorEngine`:

```python
def _fake_engine():
    from app.agent_core.engine import OrchestratorEngine
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "constraints": {"số người": [3, 4]}, "prefs": ["tiết kiệm điện"],
                                   "known": ["category", "budget_max", "constraints", "prefs"]}],
                  text_responses=["Em gợi ý máy giá 12.000.000đ và 11.000.000đ."])
    return OrchestratorEngine(_store(), llm)
```

And every `app.dependency_overrides[get_orchestrator] = _fake_orch` → `app.dependency_overrides[get_engine] = _fake_engine`. (There are 2-3 such lines; update all. Run `grep -n get_orchestrator backend/tests/test_api.py` first.)

- [ ] **Step 5: Run new + updated API tests**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_agent_api.py tests/test_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py backend/tests/test_agent_api.py
git commit -m "feat(agent_core): serve /api/chat via engine selector (PIPELINE flag)"
```

---

## Task 9: Full suite + real DeepSeek smoke test

**Files:** none (verification only), then a small doc note.

- [ ] **Step 1: Run the entire suite**

Run: `cd backend && ./.venv/Scripts/pytest -q`
Expected: all pass (64 cũ + ~30 mới). If any old test fails, it is a regression — fix before proceeding; do NOT edit tests to pass unless the contract genuinely changed.

- [ ] **Step 2: Confirm default pipeline is agent_core**

Run: `cd backend && ./.venv/Scripts/python -c "from app.config import get_settings; print(get_settings().pipeline)"`
Expected: `agent_core`.

- [ ] **Step 3: Real endpoint smoke (manual, needs network + valid key)**

Run: `cd backend && ./.venv/Scripts/python -c "from app.llm.client import get_llm; print(get_llm().complete_text('Bạn là trợ lý.', 'Chào bạn, trả lời 1 câu.')[:120])"`
Expected: a Vietnamese sentence from DeepSeek-V4-Flash (proves `LLM_API_KEY`/`LLM_MODEL`/endpoint work). If it errors, the FastAPI flow will still fall back to heuristic intent + safe summaries — note the error but it does not block merge.

- [ ] **Step 4: Live server smoke (manual)**

Run: `cd backend && ./.venv/Scripts/uvicorn app.main:app --port 8000` then in another shell:
`curl -s -X POST localhost:8000/api/chat -H "Content-Type: application/json" -d '{"session_id":"demo","message":"mua tu lanh duoi 20tr tiet kiem dien cho nha 4 nguoi"}'`
Expected: JSON with `reply`, `recommendation.cards`, `recommendation.comparison`.

- [ ] **Step 5: Update README pipeline note**

In `README.md` section 3 (structure) add one line under the backend tree noting `app/agent_core/` is now the default served pipeline (LangGraph + DeepSeek), toggle via `PIPELINE` in `.env`. Add `PIPELINE` and `AGENT_DB_PATH` rows to the env-var table in section 2.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: note agent_core is default pipeline + PIPELINE/AGENT_DB_PATH env vars"
```

---

## Self-Review

**Spec coverage:**
- Sửa đường dẫn DB → Task 1. ✅
- Giữ verify/guardrail → reused `app.advice.verify` in Tasks 5, 6; `verify_node` Task 7. ✅
- So sánh ứng viên → Task 4 + `compare_node` Task 7. ✅
- Hỏi chi tiết 1 sản phẩm → Task 5 + `detail_node` + router Task 7. ✅
- DeepSeek thay Gemini → Task 2 (intent) + Task 6 (advisor) reuse `DeepSeekClient`. ✅
- LangGraph StateGraph + MemorySaver → Task 7. ✅
- main.py phục vụ agent_core, giữ contract → Task 8; payload shape asserted. ✅
- Cờ PIPELINE + giữ 64 test → Task 8 (`OrchestratorEngine`) + Task 9. ✅
- Reset memory → Task 7 (`reset` epoch) + Task 8 endpoint. ✅
- Streaming SSE → Task 6 (`on_delta`) + Task 8 (worker/Queue). ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✅

**Type consistency:** `extract_intent(query, history, llm, db_path)` consistent T2/T7. `build_reco_card(row, priority_features)` T3 used by `build_cards` T6. `build_comparison(rows, priority_features)` T4 used by `compare_node` T7. `answer_detail(row, question, llm) -> (str, FactCard)` T5 used by `detail_node` T7. `generate_advisor(...) -> (message, streamed, warnings)` T6 used by `advisor_node` T7. `AgentCoreEngine.handle(...) -> dict` / `.reset(...)` T7 used by main.py T8. ✅

**Known risk to watch during execution:** MemorySaver persistence of runtime-only channels (`_llm`, `_on_delta`) — these are re-supplied every `invoke` via `inputs`, so stale callbacks are overwritten each turn; do not rely on their checkpointed values. If LangGraph warns about unknown input keys, prefix-underscore channels are declared in `AgentState`, so they are valid.
