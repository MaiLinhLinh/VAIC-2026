# Trợ lý AI Tư vấn Sản phẩm Điện Máy Xanh — Implementation Plan (MVP)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây một chatbot web tư vấn điện máy bằng tiếng Việt tự nhiên: hiểu nhu cầu (kể cả không dấu/viết tắt/đơn vị đời thường), chủ động hỏi ngược khi thiếu thông tin, đề xuất top-3 sản phẩm có giải thích trade-off bằng ngôn ngữ bình dân, gắn nguồn mọi con số và trả lời "chưa có dữ liệu" thay vì bịa.

**Architecture:** Pipeline mỗi lượt hội thoại: `preprocess (deterministic) → NLU parse (LLM → NeedProfile) → clarify policy (deterministic) → retrieval (structured hard-filter + deterministic preference scoring + optional semantic re-rank) → provenance fact-blocks (deterministic, grounded) → explanation generation (LLM, chỉ dùng facts được cấp) → number-grounding guardrail (deterministic)`. LLM chỉ làm 2 việc: (1) trích xuất nhu cầu thành JSON có schema, (2) diễn đạt lại các *facts đã được truy xuất* thành lời tư vấn. Mọi con số/sản phẩm đều đến từ catalog đã chuẩn hoá, không do LLM tự sinh — đây là guardrail chống hallucination.

**Tech Stack:** Python 3.12 · FastAPI · Pydantic v2 · pandas + openpyxl (ingest) · httpx (LLM client) · pytest (TDD) · React + Vite (frontend) · DeepSeek-V4-Flash qua endpoint OpenAI-compatible (cấu hình được, có `FakeLLM` để test offline). Embeddings semantic re-rank dùng `sentence-transformers` (tùy chọn, có fallback deterministic).

## Global Constraints

- **Ngôn ngữ:** Tiếng Việt bắt buộc — xử lý có dấu/không dấu, văn nói, viết tắt ("tr" = triệu, "m2" = m², "củ" = triệu), đơn vị m²/HP/BTU/GB/lít/inch/dB/W. Ưu tiên code-switching Việt-Anh trong tên/thông số.
- **Chống bịa (hard rule):** Không con số nào (giá, thông số, khuyến mãi, tồn kho) được xuất hiện trong câu trả lời nếu không truy được về một `SourcedValue` của catalog. Thiếu dữ liệu → chuỗi cố định `"chưa có dữ liệu"`. Tồn kho và review **không có** trong dataset → luôn trả lời "chưa có dữ liệu" cho hai loại này ở MVP.
- **6 category bình đẳng:** Mọi logic phụ thuộc ngành hàng phải đọc từ `category_config`, không hardcode một ngành. 6 sheet: `Tủ Lạnh`, `Máy sấy quần áo`, `Máy rửa chén`, `Tủ mát, tủ đông`, `Đồng hồ thông minh`, `Màn hình máy tính`.
- **Không PII, không lưu dữ liệu khách thật:** Session state chỉ giữ trong RAM; log phải mask nội dung tin nhắn khách khi ghi ra file.
- **Tốc độ:** phản hồi hỏi-ngược < 3s, đề xuất top-3 < 5s với dữ liệu demo (retrieval deterministic phải chạy trong RAM, không gọi LLM cho ranking).
- **On-premise friendly:** LLM sau một lớp trừu tượng `LLMClient` (base_url/api_key/model qua env); có lộ trình đổi sang model local. Retrieval/ranking/guardrail 100% chạy local.
- **Tiền tệ:** VND, số nguyên đồng. Giá gốc = `giá gốc`; giá bán thực = `giá khuyến mãi` nếu có và ≤ giá gốc, ngược lại = giá gốc.
- **Provenance nhãn:** nguồn thông số = `"thông số nhà sản xuất"`; nguồn giá = `"catalog"`; quà/khuyến mãi = `"khuyến mãi (catalog)"`.

## Data Reality (đọc kỹ trước khi code — đây là các bẫy dữ liệu thật)

- **Không có cột tên sản phẩm.** `display_name` phải tổng hợp từ `brand` + ngành + vài thông số nổi bật theo template mỗi category.
- **Đơn vị nằm trong text:** `"313 lít"`, `"46 dB"`, `"2400W"`, `"1.3 inch"`, `"300 cd/m2"`, `"1720W - 2050W"` (dải), `"4 ~ 14 lít/lần rửa"` (dải, ký tự `~`).
- **Null không đồng nhất:** `nan`, `"Không"`, `"Không có"`, `"Không cảm ứng"` — phải quy về `None`/`False` đúng ngữ cảnh.
- **Kiểu cột không nhất quán giữa sheet:** cùng ý nghĩa (vd `Cao`, `Ngang`) có sheet lưu string `"170"`, sheet khác lưu float `84.0`. Parser phải chịu cả hai.
- **Giá thường thiếu:** `giá gốc` hoặc `giá khuyến mãi` hay bị `nan` (vd Máy rửa chén Bosch row 0 thiếu cả hai giá → sản phẩm "chưa có giá").
- **Cột đa giá trị:** `Tiện ích`, `Theo dõi sức khoẻ`... dạng `"A | B | C"`.
- **`khuyến mãi quà`:** free-text rất dài, nhiều voucher; chỉ dùng để hiển thị, không parse thành số tiền giảm.
- **Số cột khác nhau:** Đồng hồ 48 cột, Máy rửa chén 30 cột.

## File Structure

```
D:\Dean'sCode\VAIC2026\
├─ Dataset.xlsx                         # nguồn (read-only)
├─ backend/
│  ├─ pyproject.toml / requirements.txt
│  ├─ .env.example
│  ├─ app/
│  │  ├─ config.py                      # Settings (env): paths, LLM base_url/key/model, weights
│  │  ├─ schemas.py                     # Pydantic: Provenance, SourcedValue, Product, NeedProfile, ...
│  │  ├─ llm/
│  │  │  ├─ client.py                   # LLMClient protocol + DeepSeekClient (httpx) + FakeLLM
│  │  ├─ catalog/
│  │  │  ├─ parsers.py                  # parse_number/range/measure/bool/people, resolve_price
│  │  │  ├─ category_config.py          # CategoryConfig cho cả 6 ngành
│  │  │  ├─ normalize.py                # row -> Product
│  │  │  ├─ loader.py                   # ProductStore (in-memory index)
│  │  ├─ nlu/
│  │  │  ├─ preprocess.py               # chuẩn hoá text deterministic (viết tắt, đơn vị)
│  │  │  ├─ parser.py                   # LLM need parser -> NeedProfile + merge
│  │  ├─ dialogue/
│  │  │  ├─ clarify.py                  # chọn câu hỏi ngược, cap, assumptions
│  │  ├─ retrieval/
│  │  │  ├─ filters.py                  # hard filter
│  │  │  ├─ scoring.py                  # preference scoring, top-3 diversity, why-not group
│  │  │  ├─ embed.py                    # semantic re-rank (optional)
│  │  │  ├─ engine.py                   # orchestrate retrieval
│  │  ├─ advice/
│  │  │  ├─ provenance.py               # FactCard builder (grounded)
│  │  │  ├─ generate.py                 # LLM explanation (grounded)
│  │  │  ├─ verify.py                   # number-grounding guardrail
│  │  │  ├─ budget.py                   # tư vấn nâng/hạ ngân sách
│  │  ├─ orchestrator.py               # state machine mỗi lượt
│  │  ├─ main.py                        # FastAPI app + session store + routes
│  ├─ scripts/build_catalog.py          # Dataset.xlsx -> data/catalog.normalized.json
│  ├─ data/catalog.normalized.json      # artifact sinh ra
│  ├─ eval/
│  │  ├─ scenarios.jsonl                # bộ test tình huống tiếng Việt
│  │  ├─ run_eval.py                    # đo need-accuracy + hallucination rate
│  └─ tests/                            # pytest, mirror app/
├─ frontend/                            # React + Vite (chat UI + panel "Vì sao?")
└─ docs/
   ├─ superpowers/plans/2026-07-17-emx-ai-advisor-mvp.md
   ├─ ARCHITECTURE.md
   └─ PILOT.md                          # lộ trình pilot (deliverable D2)
```

**Interface consistency note:** Mọi schema dùng chung được định nghĩa MỘT lần ở `app/schemas.py` (Task 2) và import ở mọi nơi. Không định nghĩa lại type ở task khác.

---

## PHASE 0 — Foundations

### Task 1: Project scaffold, deps, config, pytest bootstrap

**Files:**
- Create: `backend/requirements.txt`, `backend/.env.example`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/tests/__init__.py`, `backend/tests/test_config.py`, `backend/pytest.ini`
- Create: `.gitignore` (repo root)

**Interfaces:**
- Produces: `app.config.Settings` (pydantic-settings) với fields: `llm_base_url: str`, `llm_api_key: str`, `llm_model: str` (default `"DeepSeek-V4-Flash"`), `dataset_path: str`, `catalog_path: str`, `enable_embeddings: bool = False`; `get_settings() -> Settings` (cached).

- [ ] **Step 1: Khởi tạo git repo và .gitignore**

Run (repo root `D:\Dean'sCode\VAIC2026`):
```bash
git init
```
Create `.gitignore`:
```gitignore
__pycache__/
*.pyc
.env
.venv/
venv/
node_modules/
backend/data/catalog.normalized.json
dist/
.pytest_cache/
*.egg-info/
```

- [ ] **Step 2: requirements.txt + venv + cài đặt**

`backend/requirements.txt`:
```
fastapi==0.115.*
uvicorn[standard]==0.32.*
pydantic==2.*
pydantic-settings==2.*
pandas==2.*
openpyxl==3.*
numpy==1.*
httpx==0.27.*
python-dotenv==1.*
pytest==8.*
pytest-asyncio==0.24.*
```
Run:
```bash
cd backend && python -m venv .venv && ./.venv/Scripts/python -m pip install -U pip && ./.venv/Scripts/pip install -r requirements.txt
```
> Ghi chú Windows: dùng `./.venv/Scripts/python` (không phải `bin/`). Mọi lệnh `pytest`/`python` phía dưới đều chạy qua `./.venv/Scripts/`.

- [ ] **Step 3: .env.example + pytest.ini**

`backend/.env.example`:
```
LLM_BASE_URL=https://your-endpoint/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=DeepSeek-V4-Flash
DATASET_PATH=../Dataset.xlsx
CATALOG_PATH=./data/catalog.normalized.json
ENABLE_EMBEDDINGS=false
```
`backend/pytest.ini`:
```ini
[pytest]
pythonpath = .
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: Write the failing test** — `backend/tests/test_config.py`

```python
from app.config import get_settings

def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    s = get_settings()
    assert s.llm_model == "DeepSeek-V4-Flash"
    assert s.enable_embeddings is False
    assert s.llm_base_url == "http://x/v1"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 6: Implement** — `backend/app/config.py`

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost/v1"
    llm_api_key: str = ""
    llm_model: str = "DeepSeek-V4-Flash"
    dataset_path: str = "../Dataset.xlsx"
    catalog_path: str = "./data/catalog.normalized.json"
    enable_embeddings: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
```
Also create empty `backend/app/__init__.py` and `backend/tests/__init__.py`.

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add .gitignore backend/
git commit -m "chore: scaffold backend, config, pytest bootstrap"
```

---

### Task 2: Core Pydantic schemas

**Files:**
- Create: `backend/app/schemas.py`, `backend/tests/test_schemas.py`

**Interfaces:**
- Produces (mọi task sau import từ đây):
  - `Provenance(source: str, detail: str | None = None, as_of: str | None = None)`
  - `SourcedValue(available: bool, value=None, unit: str | None=None, provenance: Provenance | None=None, note: str | None=None)` với classmethods:
    - `SourcedValue.of(value, source, unit=None, detail=None, as_of=None) -> SourcedValue`
    - `SourcedValue.missing(note="chưa có dữ liệu") -> SourcedValue`
  - `Product(category, category_code, model_code, sku, brand, display_name, price: SourcedValue, original_price: SourcedValue, sale_price: SourcedValue, specs: dict[str, SourcedValue], spec_doc: str, promo_text: str | None, raw: dict)` với helper `number(field) -> float | None`.
  - `NeedProfile(category, budget_min, budget_max, constraints: dict, prefs: list[str], demographics: dict, known: list[str], assumptions: list[str])` với `merge(other) -> NeedProfile` (không ghi đè slot đã biết bằng None).
  - `SlotQuestion(slot, text, importance)`
  - `ScoredProduct(product, score, breakdown: dict[str,float], matched: list[str])`
  - `ExcludedGroup(label, reason)`
  - `Recommendation(top3: list[ScoredProduct], excluded: ExcludedGroup | None, assumptions: list[str])`
  - `FactLine(label, value, source)`, `FactCard(title, lines: list[FactLine], missing: list[str])`
  - `AdviceResult(message, cards: list[FactCard], assumptions: list[str], warnings: list[str])`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_schemas.py`

```python
from app.schemas import SourcedValue, NeedProfile, Product


def test_sourcedvalue_of_and_missing():
    sv = SourcedValue.of(313, "thông số nhà sản xuất", unit="lít")
    assert sv.available and sv.value == 313 and sv.unit == "lít"
    assert sv.provenance.source == "thông số nhà sản xuất"
    m = SourcedValue.missing()
    assert m.available is False and m.value is None and m.note == "chưa có dữ liệu"


def test_needprofile_merge_keeps_known():
    a = NeedProfile(category="tu_lanh", budget_max=20_000_000, known=["category", "budget_max"])
    b = NeedProfile(category=None, constraints={"số người": [3, 4]}, known=["constraints"])
    merged = a.merge(b)
    assert merged.category == "tu_lanh"
    assert merged.budget_max == 20_000_000
    assert merged.constraints == {"số người": [3, 4]}
    assert set(merged.known) == {"category", "budget_max", "constraints"}


def test_product_number_helper():
    p = Product(
        category="Tủ Lạnh", category_code="tu_lanh", model_code="1", sku="1",
        brand="Samsung", display_name="Tủ lạnh Samsung",
        price=SourcedValue.of(14990000, "catalog"),
        original_price=SourcedValue.of(14990000, "catalog"),
        sale_price=SourcedValue.missing(),
        specs={"Dung tích tổng": SourcedValue.of(313, "thông số nhà sản xuất", unit="lít"),
               "Điện năng tiêu thụ": SourcedValue.missing()},
        spec_doc="", promo_text=None, raw={},
    )
    assert p.number("Dung tích tổng") == 313
    assert p.number("Điện năng tiêu thụ") is None
    assert p.number("không tồn tại") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas'`

- [ ] **Step 3: Implement** — `backend/app/schemas.py`

```python
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source: str
    detail: str | None = None
    as_of: str | None = None


class SourcedValue(BaseModel):
    available: bool
    value: Any = None
    unit: str | None = None
    provenance: Provenance | None = None
    note: str | None = None

    @classmethod
    def of(cls, value, source, unit=None, detail=None, as_of=None) -> "SourcedValue":
        return cls(available=True, value=value, unit=unit,
                   provenance=Provenance(source=source, detail=detail, as_of=as_of))

    @classmethod
    def missing(cls, note: str = "chưa có dữ liệu") -> "SourcedValue":
        return cls(available=False, value=None, note=note)


class Product(BaseModel):
    category: str
    category_code: str
    model_code: str
    sku: str
    brand: str
    display_name: str
    price: SourcedValue
    original_price: SourcedValue
    sale_price: SourcedValue
    specs: dict[str, SourcedValue] = Field(default_factory=dict)
    spec_doc: str = ""
    promo_text: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def number(self, field: str) -> float | None:
        sv = self.specs.get(field)
        if sv is None or not sv.available:
            return None
        return sv.value if isinstance(sv.value, (int, float)) else None


class NeedProfile(BaseModel):
    category: str | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    prefs: list[str] = Field(default_factory=list)
    demographics: dict[str, str] = Field(default_factory=dict)
    known: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    def merge(self, other: "NeedProfile") -> "NeedProfile":
        out = self.model_copy(deep=True)
        for f in ("category", "budget_min", "budget_max"):
            v = getattr(other, f)
            if v is not None:
                setattr(out, f, v)
        out.constraints = {**out.constraints, **other.constraints}
        out.demographics = {**out.demographics, **other.demographics}
        out.prefs = list(dict.fromkeys(out.prefs + other.prefs))
        out.assumptions = list(dict.fromkeys(out.assumptions + other.assumptions))
        out.known = list(dict.fromkeys(out.known + other.known))
        return out


class SlotQuestion(BaseModel):
    slot: str
    text: str
    importance: int


class ScoredProduct(BaseModel):
    product: Product
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    matched: list[str] = Field(default_factory=list)


class ExcludedGroup(BaseModel):
    label: str
    reason: str


class Recommendation(BaseModel):
    top3: list[ScoredProduct]
    excluded: ExcludedGroup | None = None
    assumptions: list[str] = Field(default_factory=list)


class FactLine(BaseModel):
    label: str
    value: str
    source: str


class FactCard(BaseModel):
    title: str
    lines: list[FactLine] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class AdviceResult(BaseModel):
    message: str
    cards: list[FactCard] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_schemas.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/tests/test_schemas.py
git commit -m "feat: core pydantic schemas (SourcedValue, Product, NeedProfile, ...)"
```

---

### Task 3: LLM client (OpenAI-compatible) + FakeLLM

**Files:**
- Create: `backend/app/llm/__init__.py`, `backend/app/llm/client.py`, `backend/tests/test_llm_client.py`

**Interfaces:**
- Consumes: `app.config.get_settings`.
- Produces:
  - `LLMClient` (Protocol): `complete_json(system, user, schema_hint="") -> dict`, `complete_text(system, user) -> str`.
  - `DeepSeekClient(base_url, api_key, model)` qua httpx, POST `/chat/completions`, JSON mode `response_format={"type":"json_object"}`; `_extract_json()` bóc JSON kể cả trong ```json fences.
  - `FakeLLM(json_responses=None, text_responses=None)` — trả lần lượt phản hồi đã set (offline test).
  - `get_llm() -> LLMClient`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_llm_client.py`

```python
from app.llm.client import FakeLLM, DeepSeekClient


def test_fake_llm_returns_queued():
    fake = FakeLLM(json_responses=[{"category": "tu_lanh"}], text_responses=["Chào anh"])
    assert fake.complete_json("s", "u") == {"category": "tu_lanh"}
    assert fake.complete_text("s", "u") == "Chào anh"


def test_extract_json_handles_fences():
    raw = "```json\n{\"a\": 1}\n```"
    assert DeepSeekClient._extract_json(raw) == {"a": 1}
    assert DeepSeekClient._extract_json('{"b": 2}') == {"b": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm.client'`

- [ ] **Step 3: Implement** — `backend/app/llm/client.py`

```python
from __future__ import annotations
import json
import re
from typing import Protocol
import httpx
from app.config import get_settings


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict: ...
    def complete_text(self, system: str, user: str) -> str: ...


class DeepSeekClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _post(self, messages: list[dict], json_mode: bool) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _extract_json(raw: str) -> dict:
        raw = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1)
        else:
            brace = re.search(r"\{.*\}", raw, re.DOTALL)
            if brace:
                raw = brace.group(0)
        return json.loads(raw)

    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict:
        sys = system + ("\n\nTrả về JSON hợp lệ." + (f" Schema:\n{schema_hint}" if schema_hint else ""))
        return self._extract_json(self._post(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}], json_mode=True))

    def complete_text(self, system: str, user: str) -> str:
        return self._post(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=False)


class FakeLLM:
    def __init__(self, json_responses: list[dict] | None = None, text_responses: list[str] | None = None):
        self._json = list(json_responses or [])
        self._text = list(text_responses or [])
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, schema_hint: str = "") -> dict:
        self.calls.append((system, user))
        return self._json.pop(0) if self._json else {}

    def complete_text(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._text.pop(0) if self._text else ""


def get_llm() -> LLMClient:
    s = get_settings()
    return DeepSeekClient(s.llm_base_url, s.llm_api_key, s.llm_model)
```
Create empty `backend/app/llm/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_llm_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Smoke test thật (thủ công)**

Sau khi có `.env` thật:
```bash
cd backend && ./.venv/Scripts/python -c "from app.llm.client import get_llm; print(get_llm().complete_text('Bạn là trợ lý.','Nói xin chào bằng tiếng Việt.'))"
```
Expected: một câu chào tiếng Việt. Lỗi auth/URL → sửa `.env`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/llm/ backend/tests/test_llm_client.py
git commit -m "feat: LLM client (DeepSeek OpenAI-compatible) + FakeLLM"
```

---

## PHASE 1 — Catalog Normalization (deterministic core — heavily TDD)

### Task 4: Deterministic parsers

**Files:**
- Create: `backend/app/catalog/__init__.py`, `backend/app/catalog/parsers.py`, `backend/tests/test_parsers.py`

**Interfaces:**
- Produces:
  - `parse_number(s) -> float | None` — bóc số đầu tiên; hiểu `"313 lít"`, `"1.3 inch"`, `"300 cd/m2"`, float `84.0`, chuỗi `"170"`; `"Không"/"Không có"/nan/None/""` → `None`.
  - `parse_range(s) -> tuple[float, float] | None` — hiểu `"1720W - 2050W"`, `"4 ~ 14 lít/lần rửa"`, `"3 - 4 người"`, `"16 - 25 cm"`; số đơn → `(x, x)`.
  - `parse_measure(s) -> float | None` — dùng midpoint của range nếu là dải, ngược lại parse_number.
  - `parse_bool(s) -> bool | None` — `"Có"→True`; `"Không"/"Không có"/"Không cảm ứng"→False`; `nan→None`.
  - `parse_people(s) -> tuple[int, int] | None` — `"3 - 4 người"→(3,4)`; `"1 người"→(1,1)`.
  - `resolve_price(gia_goc, gia_km) -> tuple[SourcedValue, SourcedValue, SourcedValue]` — trả `(price, original_price, sale_price)`. Quy tắc: sale hợp lệ nếu có và `0 < sale <= original`. `price` = sale nếu hợp lệ, else original. Thiếu cả hai → `price=missing()`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_parsers.py`

```python
import math
from app.catalog.parsers import (
    parse_number, parse_range, parse_measure, parse_bool, parse_people, resolve_price,
)


def test_parse_number_units_and_types():
    assert parse_number("313 lít") == 313
    assert parse_number("1.3 inch") == 1.3
    assert parse_number("300 cd/m2") == 300
    assert parse_number("170") == 170
    assert parse_number(84.0) == 84.0
    for junk in ["Không", "Không có", "", None, float("nan")]:
        assert parse_number(junk) is None


def test_parse_range():
    assert parse_range("1720W - 2050W") == (1720.0, 2050.0)
    assert parse_range("4 ~ 14 lít/lần rửa") == (4.0, 14.0)
    assert parse_range("3 - 4 người") == (3.0, 4.0)
    assert parse_range("313 lít") == (313.0, 313.0)
    assert parse_range("Không") is None


def test_parse_measure_uses_midpoint():
    assert parse_measure("1720W - 2050W") == 1885.0
    assert parse_measure("46 dB") == 46.0


def test_parse_bool():
    assert parse_bool("Có") is True
    assert parse_bool("Không") is False
    assert parse_bool("Không có") is False
    assert parse_bool("Không cảm ứng") is False
    assert parse_bool(float("nan")) is None


def test_parse_people():
    assert parse_people("3 - 4 người") == (3, 4)
    assert parse_people("1 người") == (1, 1)
    assert parse_people("Không") is None


def test_resolve_price():
    price, orig, sale = resolve_price(14990000.0, float("nan"))
    assert price.value == 14990000 and sale.available is False
    price, orig, sale = resolve_price(19990000.0, 14990000.0)
    assert price.value == 14990000 and sale.value == 14990000 and orig.value == 19990000
    price, orig, sale = resolve_price(float("nan"), float("nan"))
    assert price.available is False and price.note == "chưa có dữ liệu"
    # sale > orig là bất thường -> bỏ sale, dùng orig
    price, orig, sale = resolve_price(10000000.0, 12000000.0)
    assert price.value == 10000000 and sale.available is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_parsers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.catalog.parsers'`

- [ ] **Step 3: Implement** — `backend/app/catalog/parsers.py`

```python
from __future__ import annotations
import math
import re
from app.schemas import SourcedValue

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
_NEG_WORDS = {"không", "không có", "không cảm ứng", "n/a", "na", "-", ""}


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def _clean(s) -> str | None:
    if s is None or _is_nan(s):
        return None
    text = str(s).strip()
    if text.lower() in _NEG_WORDS:
        return None
    return text


def _to_float(tok: str) -> float:
    return float(tok.replace(",", "."))


def parse_number(s) -> float | None:
    if isinstance(s, (int, float)) and not _is_nan(s):
        return float(s)
    text = _clean(s)
    if text is None:
        return None
    m = _NUM.search(text)
    return _to_float(m.group(0)) if m else None


def parse_range(s) -> tuple[float, float] | None:
    if isinstance(s, (int, float)) and not _is_nan(s):
        return (float(s), float(s))
    text = _clean(s)
    if text is None:
        return None
    nums = _NUM.findall(text)
    if not nums:
        return None
    if len(nums) == 1:
        v = _to_float(nums[0])
        return (v, v)
    lo, hi = _to_float(nums[0]), _to_float(nums[1])
    return (min(lo, hi), max(lo, hi))


def parse_measure(s) -> float | None:
    r = parse_range(s)
    if r is None:
        return None
    lo, hi = r
    return (lo + hi) / 2 if lo != hi else lo


def parse_bool(s) -> bool | None:
    if _is_nan(s) or s is None:
        return None
    text = str(s).strip().lower()
    if text == "":
        return None
    if text.startswith("không"):
        return False
    if text in {"có", "co"}:
        return True
    return None


def parse_people(s) -> tuple[int, int] | None:
    r = parse_range(s)
    if r is None:
        return None
    return (int(r[0]), int(r[1]))


def resolve_price(gia_goc, gia_km):
    orig_n = parse_number(gia_goc)
    sale_n = parse_number(gia_km)
    orig = SourcedValue.of(int(orig_n), "catalog") if orig_n else SourcedValue.missing()
    sale_valid = sale_n is not None and sale_n > 0 and (orig_n is None or sale_n <= orig_n)
    sale = SourcedValue.of(int(sale_n), "catalog") if sale_valid else SourcedValue.missing()
    if sale_valid:
        price = SourcedValue.of(int(sale_n), "catalog", detail="giá khuyến mãi")
    elif orig_n:
        price = SourcedValue.of(int(orig_n), "catalog", detail="giá gốc")
    else:
        price = SourcedValue.missing()
    return price, orig, sale
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_parsers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/catalog/ backend/tests/test_parsers.py
git commit -m "feat: deterministic value parsers + price resolution"
```

---

### Task 5: Category configs (cả 6 ngành)

**Files:**
- Create: `backend/app/catalog/category_config.py`, `backend/tests/test_category_config.py`

**Interfaces:**
- Produces:
  - `SpecKind` = Literal["number", "range", "bool", "people", "text", "multi"]
  - `SpecDef(field: str, kind: SpecKind, unit: str | None = None)`
  - `SlotSpec(slot, question, importance, maps_to, kind)` — `maps_to` là key trong `NeedProfile.constraints`; `importance` 1..3 (3 = critical).
  - `PrefSignal(field: str, direction: Literal["min","max"], weight: float)`
  - `ExclusionRule(when_pref: str, label: str, field: str, empty_means_bad: bool)`
  - `CategoryConfig(code, sheet_name, display, name_template, specs: list[SpecDef], spec_doc_fields: list[str], ask_slots: list[SlotSpec], pref_lexicon: dict[str, list[PrefSignal]], exclusion_rules: list[ExclusionRule])`
  - `CATEGORY_CONFIGS: dict[str, CategoryConfig]` keyed by `code` (6 entries).
  - `SHEET_TO_CODE: dict[str, str]`; `config_for(code) -> CategoryConfig`.
  - Codes: `tu_lanh`, `may_say`, `may_rua_chen`, `tu_mat`, `dong_ho`, `man_hinh`.

> Ghi chú: `name_template` dùng `{brand}` + tối đa 2 field text; field thiếu bỏ qua sạch. `pref_lexicon` map cụm ưu tiên chuẩn hoá (vd `"tiết kiệm điện"`, `"ít ồn"`, `"dung tích lớn"`, `"màn hình lớn"`, `"pin lâu"`) → tín hiệu chấm điểm trên field số. Chỉ khai báo field CÓ trong sheet đó.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_category_config.py`

```python
from app.catalog.category_config import CATEGORY_CONFIGS, config_for, SHEET_TO_CODE


def test_all_six_categories_present():
    assert set(CATEGORY_CONFIGS) == {
        "tu_lanh", "may_say", "may_rua_chen", "tu_mat", "dong_ho", "man_hinh"}


def test_sheet_mapping():
    assert SHEET_TO_CODE["Tủ Lạnh"] == "tu_lanh"
    assert SHEET_TO_CODE["Đồng hồ thông minh"] == "dong_ho"


def test_tu_lanh_has_critical_slots_and_prefs():
    cfg = config_for("tu_lanh")
    assert cfg.display == "Tủ lạnh"
    # có ít nhất 1 slot critical (importance 3)
    assert any(s.importance == 3 for s in cfg.ask_slots)
    # ưu tiên "tiết kiệm điện" phải map tới field điện năng, direction min
    sigs = cfg.pref_lexicon["tiết kiệm điện"]
    assert any(sig.field == "Điện năng tiêu thụ" and sig.direction == "min" for sig in sigs)


def test_pref_signal_fields_exist_in_specs():
    # mọi field trong lexicon & exclusion phải là spec đã khai báo (tránh lệch tên)
    for cfg in CATEGORY_CONFIGS.values():
        spec_fields = {s.field for s in cfg.specs}
        for sigs in cfg.pref_lexicon.values():
            for sig in sigs:
                assert sig.field in spec_fields, f"{cfg.code}: {sig.field} không có trong specs"
        for rule in cfg.exclusion_rules:
            assert rule.field in spec_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_category_config.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/catalog/category_config.py`

```python
from __future__ import annotations
from typing import Literal
from dataclasses import dataclass, field

SpecKind = Literal["number", "range", "bool", "people", "text", "multi"]


@dataclass(frozen=True)
class SpecDef:
    field: str
    kind: SpecKind
    unit: str | None = None


@dataclass(frozen=True)
class SlotSpec:
    slot: str
    question: str
    importance: int          # 3 = critical, 2 = nên hỏi, 1 = tùy chọn
    maps_to: str
    kind: SpecKind


@dataclass(frozen=True)
class PrefSignal:
    field: str
    direction: Literal["min", "max"]
    weight: float = 1.0


@dataclass(frozen=True)
class ExclusionRule:
    when_pref: str
    label: str
    field: str
    empty_means_bad: bool = True


@dataclass(frozen=True)
class CategoryConfig:
    code: str
    sheet_name: str
    display: str
    name_template: str
    specs: list[SpecDef]
    spec_doc_fields: list[str]
    ask_slots: list[SlotSpec]
    pref_lexicon: dict[str, list[PrefSignal]]
    exclusion_rules: list[ExclusionRule] = field(default_factory=list)


CATEGORY_CONFIGS: dict[str, CategoryConfig] = {
    "tu_lanh": CategoryConfig(
        code="tu_lanh", sheet_name="Tủ Lạnh", display="Tủ lạnh",
        name_template="Tủ lạnh {brand} {Công nghệ tiết kiệm điện} {Dung tích tổng}",
        specs=[
            SpecDef("Dung tích tổng", "number", "lít"),
            SpecDef("Điện năng tiêu thụ", "number", "kWh/năm"),
            SpecDef("Số người sử dụng", "people", "người"),
            SpecDef("Kiểu dáng", "text"),
            SpecDef("Công nghệ tiết kiệm điện", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Kiểu dáng", "Công nghệ làm lạnh", "Công nghệ tiết kiệm điện",
                         "Công nghệ bảo quản thực phẩm", "Tiện ích"],
        ask_slots=[
            SlotSpec("số người", "Nhà mình khoảng mấy người dùng tủ lạnh này ạ?", 3, "số người", "people"),
            SlotSpec("kiểu dáng", "Anh/chị thích kiểu ngăn đá trên hay ngăn đá dưới ạ?", 1, "kiểu dáng", "text"),
        ],
        pref_lexicon={
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
            "dung tích lớn": [PrefSignal("Dung tích tổng", "max", 1.0)],
            "gia đình đông": [PrefSignal("Dung tích tổng", "max", 1.0)],
        },
        exclusion_rules=[
            ExclusionRule("tiết kiệm điện", "tủ lạnh không inverter", "Công nghệ tiết kiệm điện", True),
        ],
    ),
    "may_say": CategoryConfig(
        code="may_say", sheet_name="Máy sấy quần áo", display="Máy sấy quần áo",
        name_template="Máy sấy {brand} {Khối lượng tải chính}",
        specs=[
            SpecDef("Khối lượng tải chính", "number", "kg"),
            SpecDef("Điện năng tiêu thụ", "number"),
            SpecDef("Số người sử dụng", "people", "người"),
            SpecDef("Công nghệ", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Loại sản phẩm", "Công nghệ", "Tiện ích", "Cảm biến"],
        ask_slots=[
            SlotSpec("khối lượng", "Nhà mình cần sấy khoảng mấy kg mỗi lần ạ (nhà mấy người)?", 3, "khối lượng", "number"),
        ],
        pref_lexicon={
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
            "tải lớn": [PrefSignal("Khối lượng tải chính", "max", 1.0)],
        },
        exclusion_rules=[],
    ),
    "may_rua_chen": CategoryConfig(
        code="may_rua_chen", sheet_name="Máy rửa chén", display="Máy rửa chén",
        name_template="Máy rửa chén {brand} {Loại sản phẩm}",
        specs=[
            SpecDef("Độ ồn", "number", "dB"),
            SpecDef("Tiêu thụ nước", "range", "lít/lần"),
            SpecDef("Công suất đầu ra", "range", "W"),
            SpecDef("Loại sản phẩm", "text"),
            SpecDef("Công nghệ", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Loại sản phẩm", "Công nghệ", "Công nghệ sấy", "Chương trình", "Tiện ích"],
        ask_slots=[
            SlotSpec("số bữa", "Nhà mình khoảng mấy người ăn để em tính số bộ chén phù hợp ạ?", 3, "số người", "people"),
        ],
        pref_lexicon={
            "ít ồn": [PrefSignal("Độ ồn", "min", 1.0)],
            "tiết kiệm nước": [PrefSignal("Tiêu thụ nước", "min", 1.0)],
        },
        exclusion_rules=[],
    ),
    "tu_mat": CategoryConfig(
        code="tu_mat", sheet_name="Tủ mát, tủ đông", display="Tủ mát / tủ đông",
        name_template="{Loại sản phẩm} {brand} {Dung tích tổng}",
        specs=[
            SpecDef("Dung tích tổng", "number", "lít"),
            SpecDef("Điện năng tiêu thụ", "number"),
            SpecDef("Độ ồn", "number", "dB"),
            SpecDef("Loại sản phẩm", "text"),
            SpecDef("Số cửa", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Loại sản phẩm", "Công nghệ", "Tiện ích", "Số cửa"],
        ask_slots=[
            SlotSpec("dung tích", "Anh/chị cần dung tích khoảng bao nhiêu lít, hay để em gợi ý theo nhu cầu ạ?", 2, "dung tích", "number"),
        ],
        pref_lexicon={
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
            "dung tích lớn": [PrefSignal("Dung tích tổng", "max", 1.0)],
            "ít ồn": [PrefSignal("Độ ồn", "min", 1.0)],
        },
        exclusion_rules=[],
    ),
    "dong_ho": CategoryConfig(
        code="dong_ho", sheet_name="Đồng hồ thông minh", display="Đồng hồ thông minh",
        name_template="Đồng hồ {brand} {Kích thước mặt}",
        specs=[
            SpecDef("Dung lượng pin", "number", "mAh"),
            SpecDef("Kích thước màn hình", "number", "inch"),
            SpecDef("SIM", "text"),
            SpecDef("Thực hiện cuộc gọi", "text"),
            SpecDef("Theo dõi sức khoẻ", "multi"),
            SpecDef("Môn thể thao", "multi"),
        ],
        spec_doc_fields=["Theo dõi sức khoẻ", "Môn thể thao", "Tiện ích khác",
                         "Thực hiện cuộc gọi", "Chuẩn chống nước, bụi"],
        ask_slots=[
            SlotSpec("người dùng", "Đồng hồ này dùng cho ai ạ (trẻ em, người lớn, người tập thể thao)?", 3, "người dùng", "text"),
        ],
        pref_lexicon={
            "pin lâu": [PrefSignal("Dung lượng pin", "max", 1.0)],
            "màn hình lớn": [PrefSignal("Kích thước màn hình", "max", 1.0)],
        },
        exclusion_rules=[],
    ),
    "man_hinh": CategoryConfig(
        code="man_hinh", sheet_name="Màn hình máy tính", display="Màn hình máy tính",
        name_template="Màn hình {brand} {Kích thước màn hình} {Tấm nền}",
        specs=[
            SpecDef("Kích thước màn hình", "number", "inch"),
            SpecDef("Thời gian đáp ứng", "number", "ms"),
            SpecDef("Điện năng tiêu thụ", "number", "W"),
            SpecDef("Tấm nền", "text"),
            SpecDef("Độ phân giải", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Tấm nền", "Độ phân giải", "Màn hình hiển thị", "Tiện ích", "Loại màn hình"],
        ask_slots=[
            SlotSpec("mục đích", "Anh/chị dùng màn hình chủ yếu để làm gì ạ (văn phòng, chơi game, đồ họa)?", 3, "mục đích", "text"),
            SlotSpec("kích thước", "Anh/chị muốn màn khoảng bao nhiêu inch ạ?", 2, "kích thước", "number"),
        ],
        pref_lexicon={
            "màn hình lớn": [PrefSignal("Kích thước màn hình", "max", 1.0)],
            "chơi game": [PrefSignal("Thời gian đáp ứng", "min", 1.0)],
            "phản hồi nhanh": [PrefSignal("Thời gian đáp ứng", "min", 1.0)],
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
        },
        exclusion_rules=[],
    ),
}

SHEET_TO_CODE: dict[str, str] = {c.sheet_name: c.code for c in CATEGORY_CONFIGS.values()}


def config_for(code: str) -> CategoryConfig:
    return CATEGORY_CONFIGS[code]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_category_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/catalog/category_config.py backend/tests/test_category_config.py
git commit -m "feat: per-category config for all 6 categories"
```

---

### Task 6: Normalizer + build script + ProductStore loader

**Files:**
- Create: `backend/app/catalog/normalize.py`, `backend/app/catalog/loader.py`, `backend/scripts/build_catalog.py`, `backend/tests/test_normalize.py`, `backend/tests/test_loader.py`
- Generate (artifact): `backend/data/catalog.normalized.json`

**Interfaces:**
- Consumes: `parsers`, `category_config`, `schemas.Product`.
- Produces:
  - `normalize_row(row: dict, cfg: CategoryConfig) -> Product` — parse mỗi spec theo `kind`; build `display_name` (bỏ field thiếu); `spec_doc` = join text các `spec_doc_fields`; `resolve_price` từ `giá gốc`/`giá khuyến mãi`; `promo_text` từ `khuyến mãi quà`.
  - `build_catalog(xlsx_path) -> list[Product]` — lặp 6 sheet.
  - `save_catalog(products, path)` / `load_catalog(path) -> list[Product]`.
  - `ProductStore(products)` với `.by_category(code) -> list[Product]`, `.all() -> list[Product]`, `.get_store() -> ProductStore` (cached singleton từ `settings.catalog_path`).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_normalize.py`

```python
from app.catalog.normalize import normalize_row
from app.catalog.category_config import config_for


def test_normalize_tu_lanh_row():
    cfg = config_for("tu_lanh")
    row = {
        "model_code": 165156, "sku": 1751097000147, "brand": "Samsung",
        "Kiểu dáng": "Ngăn đá dưới", "Dung tích tổng": "313 lít",
        "Điện năng tiêu thụ": "381", "Số người sử dụng": "3 - 4 người",
        "Công nghệ tiết kiệm điện": "Digital Inverter",
        "Công nghệ làm lạnh": "All-around Cooling", "Tiện ích": "Auto Ice Maker | Đèn LED",
        "Công nghệ bảo quản thực phẩm": "Optimal Fresh",
        "giá gốc": 14990000.0, "giá khuyến mãi": float("nan"),
        "khuyến mãi quà": "Miễn phí công lắp đặt",
    }
    p = normalize_row(row, cfg)
    assert p.category_code == "tu_lanh"
    assert p.number("Dung tích tổng") == 313
    assert p.number("Điện năng tiêu thụ") == 381
    assert p.specs["Số người sử dụng"].value == [3, 4]
    assert p.price.value == 14990000 and p.sale_price.available is False
    assert "Samsung" in p.display_name and "313" in p.display_name
    assert "Inverter" in p.spec_doc  # spec_doc gộp field text
    assert p.promo_text == "Miễn phí công lắp đặt"


def test_normalize_missing_price_marks_unavailable():
    cfg = config_for("may_rua_chen")
    row = {"model_code": 1, "sku": 2, "brand": "Bosch", "Loại sản phẩm": "Độc lập",
           "Độ ồn": "46 dB", "giá gốc": float("nan"), "giá khuyến mãi": float("nan")}
    p = normalize_row(row, cfg)
    assert p.price.available is False and p.price.note == "chưa có dữ liệu"
    assert p.number("Độ ồn") == 46
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/catalog/normalize.py`

```python
from __future__ import annotations
import math
import re
import pandas as pd
from app.schemas import Product, SourcedValue
from app.catalog.parsers import parse_number, parse_range, parse_measure, parse_bool, parse_people, resolve_price
from app.catalog.category_config import CategoryConfig, CATEGORY_CONFIGS

_SRC_SPEC = "thông số nhà sản xuất"


def _is_nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _text(x) -> str | None:
    if _is_nan(x):
        return None
    t = str(x).strip()
    return t or None


def _spec_value(raw, kind: str, unit: str | None) -> SourcedValue:
    if kind == "number":
        v = parse_number(raw)
    elif kind == "range":
        v = parse_measure(raw)
    elif kind == "people":
        pr = parse_people(raw)
        v = list(pr) if pr else None
    elif kind == "bool":
        v = parse_bool(raw)
    else:  # text, multi
        v = _text(raw)
    if v is None:
        return SourcedValue.missing()
    return SourcedValue.of(v, _SRC_SPEC, unit=unit)


def _build_name(template: str, row: dict, brand: str) -> str:
    def repl(m):
        key = m.group(1)
        if key == "brand":
            return brand
        return _text(row.get(key)) or ""
    name = re.sub(r"\{([^}]+)\}", repl, template)
    return re.sub(r"\s+", " ", name).strip()


def normalize_row(row: dict, cfg: CategoryConfig) -> Product:
    brand = _text(row.get("brand")) or "?"
    specs: dict[str, SourcedValue] = {}
    for sd in cfg.specs:
        specs[sd.field] = _spec_value(row.get(sd.field), sd.kind, sd.unit)
    doc_parts = [_text(row.get(f)) for f in cfg.spec_doc_fields]
    spec_doc = " | ".join(p for p in doc_parts if p)
    price, orig, sale = resolve_price(row.get("giá gốc"), row.get("giá khuyến mãi"))
    return Product(
        category=cfg.display, category_code=cfg.code,
        model_code=str(row.get("model_code")), sku=str(row.get("sku")),
        brand=brand, display_name=_build_name(cfg.name_template, row, brand),
        price=price, original_price=orig, sale_price=sale,
        specs=specs, spec_doc=spec_doc,
        promo_text=_text(row.get("khuyến mãi quà")),
        raw={k: (None if _is_nan(v) else v) for k, v in row.items()},
    )


def build_catalog(xlsx_path: str) -> list[Product]:
    products: list[Product] = []
    for cfg in CATEGORY_CONFIGS.values():
        df = pd.read_excel(xlsx_path, sheet_name=cfg.sheet_name)
        for rec in df.to_dict(orient="records"):
            products.append(normalize_row(rec, cfg))
    return products
```

- [ ] **Step 4: Implement loader** — `backend/app/catalog/loader.py`

```python
from __future__ import annotations
import json
from functools import lru_cache
from app.schemas import Product
from app.config import get_settings


def save_catalog(products: list[Product], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in products], f, ensure_ascii=False)


def load_catalog(path: str) -> list[Product]:
    with open(path, encoding="utf-8") as f:
        return [Product(**d) for d in json.load(f)]


class ProductStore:
    def __init__(self, products: list[Product]):
        self._all = products
        self._by_cat: dict[str, list[Product]] = {}
        for p in products:
            self._by_cat.setdefault(p.category_code, []).append(p)

    def all(self) -> list[Product]:
        return self._all

    def by_category(self, code: str) -> list[Product]:
        return self._by_cat.get(code, [])


@lru_cache
def get_store() -> ProductStore:
    return ProductStore(load_catalog(get_settings().catalog_path))
```

- [ ] **Step 5: Build script** — `backend/scripts/build_catalog.py`

```python
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.catalog.normalize import build_catalog
from app.catalog.loader import save_catalog
from app.config import get_settings


def main():
    s = get_settings()
    products = build_catalog(s.dataset_path)
    os.makedirs(os.path.dirname(s.catalog_path), exist_ok=True)
    save_catalog(products, s.catalog_path)
    print(f"Normalized {len(products)} products -> {s.catalog_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run normalize test**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_normalize.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Build artifact thật + kiểm tra sơ bộ**

```bash
cd backend && ./.venv/Scripts/python scripts/build_catalog.py
```
Expected: `Normalized 3960 products -> ./data/catalog.normalized.json` (±, tổng 6 sheet).

- [ ] **Step 8: Write loader test** — `backend/tests/test_loader.py`

```python
from app.catalog.loader import load_catalog, ProductStore
from app.config import get_settings


def test_store_loads_all_six_categories():
    products = load_catalog(get_settings().catalog_path)
    store = ProductStore(products)
    codes = {p.category_code for p in store.all()}
    assert codes == {"tu_lanh", "may_say", "may_rua_chen", "tu_mat", "dong_ho", "man_hinh"}
    assert len(store.by_category("tu_lanh")) > 1000
```

Run: `cd backend && ./.venv/Scripts/pytest tests/test_loader.py -v`
Expected: PASS (yêu cầu đã chạy Step 7 để có artifact).

- [ ] **Step 9: Commit**

```bash
git add backend/app/catalog/normalize.py backend/app/catalog/loader.py backend/scripts/ backend/tests/test_normalize.py backend/tests/test_loader.py
git commit -m "feat: catalog normalizer, build script, in-memory ProductStore"
```

---

## PHASE 2 — NLU & Dialogue

### Task 7: Deterministic text preprocessing

**Files:**
- Create: `backend/app/nlu/__init__.py`, `backend/app/nlu/preprocess.py`, `backend/tests/test_preprocess.py`

**Interfaces:**
- Produces:
  - `strip_accents(s) -> str` — bỏ dấu tiếng Việt (để so khớp không dấu).
  - `expand_shorthand(s) -> str` — chuẩn hoá tiền/đơn vị viết tắt về dạng LLM dễ đọc: `"20tr"→"20 triệu"`, `"20t"→"20 triệu"`, `"500k"→"500 nghìn"`, `"18m2"→"18 m²"`, `"1hp"→"1 HP"`. Không suy diễn ngữ nghĩa, chỉ nở token.
  - `parse_budget_vnd(s) -> tuple[int | None, int | None]` — bóc ngân sách VND deterministic (backup cho LLM): `"dưới 20 triệu"→(None, 20000000)`, `"trên 5 triệu"→(5000000, None)`, `"khoảng 10-15 triệu"→(10000000, 15000000)`, `"12tr"→(None, 12000000)` (mặc định coi 1 số là trần).
  - `detect_category(s) -> str | None` — dò category theo từ khoá không dấu: `"tủ lạnh/tu lanh"→tu_lanh`, `"đồng hồ/dong ho/smartwatch"→dong_ho`, `"màn hình/man hinh/monitor"→man_hinh`, `"máy sấy"→may_say`, `"rửa chén/rua chen"→may_rua_chen`, `"tủ mát/tủ đông/tu dong"→tu_mat`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_preprocess.py`

```python
from app.nlu.preprocess import strip_accents, expand_shorthand, parse_budget_vnd, detect_category


def test_strip_accents():
    assert strip_accents("Tủ Lạnh tiết kiệm điện") == "Tu Lanh tiet kiem dien"


def test_expand_shorthand():
    out = expand_shorthand("mua may lanh 20tr cho phong 18m2")
    assert "20 triệu" in out and "18 m²" in out


def test_parse_budget_vnd():
    assert parse_budget_vnd("dưới 20 triệu") == (None, 20_000_000)
    assert parse_budget_vnd("trên 5 triệu") == (5_000_000, None)
    assert parse_budget_vnd("khoảng 10-15 triệu") == (10_000_000, 15_000_000)
    assert parse_budget_vnd("12tr") == (None, 12_000_000)
    assert parse_budget_vnd("500k") == (None, 500_000)


def test_detect_category_no_accents():
    assert detect_category("e muon mua tu lanh") == "tu_lanh"
    assert detect_category("cần cái đồng hồ thông minh") == "dong_ho"
    assert detect_category("mua màn hình gaming") == "man_hinh"
    assert detect_category("xin chào") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_preprocess.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/nlu/preprocess.py`

```python
from __future__ import annotations
import re
import unicodedata

_CATEGORY_KEYWORDS = {
    "tu_lanh": ["tu lanh", "refrigerator"],
    "may_say": ["may say"],
    "may_rua_chen": ["rua chen", "may rua chen", "dishwasher"],
    "tu_mat": ["tu mat", "tu dong", "freezer"],
    "dong_ho": ["dong ho", "smartwatch", "smart watch", "watch"],
    "man_hinh": ["man hinh", "monitor", "screen"],
}


def strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s)
    out = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return out.replace("đ", "d").replace("Đ", "D")


def expand_shorthand(s: str) -> str:
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*(tr|trieu|triệu|t)\b", r"\1 triệu", s, flags=re.I)
    s = re.sub(r"(\d+)\s*(k|nghin|nghìn)\b", r"\1 nghìn", s, flags=re.I)
    s = re.sub(r"(\d+)\s*m2\b", r"\1 m²", s, flags=re.I)
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*hp\b", r"\1 HP", s, flags=re.I)
    return s


def _to_vnd(num: float, unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("tri") or unit == "t":
        return int(num * 1_000_000)
    if unit.startswith("ngh") or unit == "k":
        return int(num * 1_000)
    return int(num)


def parse_budget_vnd(s: str):
    txt = s.lower()
    rng = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(tri[eệ]u|tr|k|ngh[iì]n)", txt)
    if rng:
        u = rng.group(3)
        lo = _to_vnd(float(rng.group(1).replace(",", ".")), u)
        hi = _to_vnd(float(rng.group(2).replace(",", ".")), u)
        return (lo, hi)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(tri[eệ]u|tr|k|ngh[iì]n)\b", txt)
    if not m:
        return (None, None)
    val = _to_vnd(float(m.group(1).replace(",", ".")), m.group(2))
    if "trên" in txt or "tren" in txt or "từ" in txt:
        return (val, None)
    return (None, val)  # mặc định: 1 con số = trần ngân sách


def detect_category(s: str) -> str | None:
    flat = strip_accents(s.lower())
    for code, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in flat for kw in kws):
            return code
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_preprocess.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/nlu/preprocess.py backend/tests/test_preprocess.py
git commit -m "feat: deterministic Vietnamese text preprocessing"
```

---

### Task 8: LLM need parser → NeedProfile

**Files:**
- Create: `backend/app/nlu/parser.py`, `backend/tests/test_parser.py`

**Interfaces:**
- Consumes: `LLMClient`, `preprocess`, `schemas.NeedProfile`, `category_config.CATEGORY_CONFIGS`.
- Produces:
  - `NEED_SYSTEM_PROMPT: str`, `NEED_SCHEMA_HINT: str`.
  - `parse_need(message: str, llm: LLMClient, prior: NeedProfile | None = None) -> NeedProfile` — chạy `expand_shorthand`, gọi LLM lấy JSON, validate về `NeedProfile`, chèn deterministic fallback: nếu LLM bỏ sót `category`/budget thì lấy từ `detect_category`/`parse_budget_vnd`. Cập nhật `known` cho mọi slot LLM điền. Merge với `prior` (giữ slot đã biết). Câu "cứ gợi ý đại đi / sao cũng được" → set `constraints["_khong_muon_tra_loi"]=True`.
- Guardrail parser: **chỉ trích những gì khách NÓI**; không tự suy field khách chưa nêu (đúng yêu cầu VAIC 2.1 "chưa biết, không tự đoán"). Prompt phải nhấn mạnh điều này.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_parser.py`

```python
from app.llm.client import FakeLLM
from app.nlu.parser import parse_need
from app.schemas import NeedProfile


def test_parse_need_maps_llm_json():
    fake = FakeLLM(json_responses=[{
        "category": "tu_lanh", "budget_max": 20000000, "budget_min": None,
        "constraints": {"số người": [3, 4]}, "prefs": ["tiết kiệm điện", "ít ồn"],
        "demographics": {}, "known": ["category", "budget_max", "constraints", "prefs"],
    }])
    np_ = parse_need("mua tu lanh duoi 20tr cho gia dinh 4 nguoi, tiet kiem dien, it on", fake)
    assert np_.category == "tu_lanh"
    assert np_.budget_max == 20_000_000
    assert np_.prefs == ["tiết kiệm điện", "ít ồn"]
    assert "category" in np_.known


def test_parse_need_deterministic_fallback_when_llm_misses_category():
    fake = FakeLLM(json_responses=[{"category": None, "prefs": [], "constraints": {}, "known": []}])
    np_ = parse_need("cho minh cai man hinh gaming duoi 5 trieu", fake)
    assert np_.category == "man_hinh"       # detect_category bù
    assert np_.budget_max == 5_000_000       # parse_budget_vnd bù
    assert "category" in np_.known and "budget_max" in np_.known


def test_parse_need_merges_prior_keeping_known():
    prior = NeedProfile(category="tu_lanh", budget_max=20_000_000, known=["category", "budget_max"])
    fake = FakeLLM(json_responses=[{"category": None, "constraints": {"số người": [3, 4]},
                                    "prefs": [], "known": ["constraints"]}])
    np_ = parse_need("nhà mình 4 người", fake, prior=prior)
    assert np_.category == "tu_lanh" and np_.budget_max == 20_000_000
    assert np_.constraints == {"số người": [3, 4]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/nlu/parser.py`

```python
from __future__ import annotations
from app.schemas import NeedProfile
from app.llm.client import LLMClient
from app.nlu.preprocess import expand_shorthand, parse_budget_vnd, detect_category

NEED_SYSTEM_PROMPT = (
    "Bạn là bộ phân tích nhu cầu mua điện máy. Chỉ trích xuất những gì khách HÀNG NÓI RÕ. "
    "TUYỆT ĐỐI không suy đoán thông tin khách chưa nêu — thông tin thiếu để trống (null), "
    "không tự bịa. category chỉ nhận một trong: "
    "tu_lanh, may_say, may_rua_chen, tu_mat, dong_ho, man_hinh (hoặc null nếu không rõ). "
    "budget tính bằng VND (số nguyên đồng). prefs là các cụm ưu tiên ngắn gọn tiếng Việt có dấu "
    "(vd: 'tiết kiệm điện', 'ít ồn', 'pin lâu', 'màn hình lớn', 'chơi game'). "
    "constraints chứa ràng buộc cứng khách nêu (vd số người, kích thước). "
    "demographics chứa suy luận nhân khẩu học CHỈ khi khách nói rõ (vd 'cho bé' -> {\"đối tượng\":\"trẻ em\"}). "
    "known liệt kê tên các trường đã điền được."
)

NEED_SCHEMA_HINT = (
    '{"category": "tu_lanh|null", "budget_min": int|null, "budget_max": int|null, '
    '"constraints": {}, "prefs": [], "demographics": {}, "known": []}'
)

_VALID_CODES = {"tu_lanh", "may_say", "may_rua_chen", "tu_mat", "dong_ho", "man_hinh"}
_DECLINE_PHRASES = ["gợi ý đại", "goi y dai", "sao cũng được", "sao cung duoc", "tùy em", "tuy em", "gì cũng được"]


def _to_profile(data: dict) -> NeedProfile:
    cat = data.get("category")
    if cat not in _VALID_CODES:
        cat = None
    return NeedProfile(
        category=cat,
        budget_min=data.get("budget_min"),
        budget_max=data.get("budget_max"),
        constraints=data.get("constraints") or {},
        prefs=data.get("prefs") or [],
        demographics=data.get("demographics") or {},
        known=list(data.get("known") or []),
    )


def parse_need(message: str, llm: LLMClient, prior: NeedProfile | None = None) -> NeedProfile:
    expanded = expand_shorthand(message)
    raw = llm.complete_json(NEED_SYSTEM_PROMPT, expanded, schema_hint=NEED_SCHEMA_HINT)
    prof = _to_profile(raw)

    # Deterministic fallback: bù category & budget nếu LLM bỏ sót
    if prof.category is None:
        det = detect_category(message)
        if det:
            prof.category = det
            prof.known.append("category")
    if prof.budget_max is None and prof.budget_min is None:
        lo, hi = parse_budget_vnd(expanded)
        if hi is not None:
            prof.budget_max = hi
            prof.known.append("budget_max")
        if lo is not None:
            prof.budget_min = lo
            prof.known.append("budget_min")

    flat = message.lower()
    if any(p in flat for p in _DECLINE_PHRASES):
        prof.constraints["_khong_muon_tra_loi"] = True
        prof.known.append("_khong_muon_tra_loi")

    prof.known = list(dict.fromkeys(prof.known))
    return prior.merge(prof) if prior else prof
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_parser.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/nlu/parser.py backend/tests/test_parser.py
git commit -m "feat: LLM need parser -> NeedProfile with deterministic fallback"
```

---

### Task 9: Clarification policy (hỏi ngược)

**Files:**
- Create: `backend/app/dialogue/__init__.py`, `backend/app/dialogue/clarify.py`, `backend/tests/test_clarify.py`

**Interfaces:**
- Consumes: `NeedProfile`, `category_config` (`ask_slots`, importance).
- Produces:
  - `MAX_QUESTIONS = 3`
  - `missing_critical_slots(profile, asked: list[str]) -> list[SlotSpec]` — slot importance ≥ 2 chưa có trong `constraints` và chưa hỏi; sắp theo importance giảm dần.
  - `next_question(profile, asked) -> SlotQuestion | None` — trả câu hỏi quan trọng nhất còn thiếu; `None` nếu: đã đủ, đã hỏi đủ `MAX_QUESTIONS`, hoặc khách từ chối trả lời (`_khong_muon_tra_loi`).
  - `should_recommend(profile, asked) -> bool` — True khi `next_question is None` và đã biết `category`.
  - `assumptions_for(profile, asked) -> list[str]` — khi bỏ qua slot chưa biết lúc chốt đề xuất, sinh câu giả định minh bạch (vd "Em tạm tính theo nhà 3–4 người nhé, nếu khác anh/chị báo em tính lại.").

- [ ] **Step 1: Write the failing test** — `backend/tests/test_clarify.py`

```python
from app.dialogue.clarify import next_question, should_recommend, missing_critical_slots, assumptions_for
from app.schemas import NeedProfile


def test_asks_critical_slot_first():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, prefs=["tiết kiệm điện"])
    q = next_question(prof, asked=[])
    assert q is not None and q.slot == "số người"     # importance 3 của tủ lạnh


def test_no_question_when_critical_filled():
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, constraints={"số người": [3, 4]})
    assert next_question(prof, asked=["kiểu dáng"]) is None
    assert should_recommend(prof, asked=["kiểu dáng"]) is True


def test_stops_after_max_questions():
    prof = NeedProfile(category="man_hinh")
    assert next_question(prof, asked=["mục đích", "kích thước", "x"]) is None


def test_decline_skips_questions_and_adds_assumption():
    prof = NeedProfile(category="tu_lanh", constraints={"_khong_muon_tra_loi": True})
    assert next_question(prof, asked=[]) is None
    assert should_recommend(prof, asked=[]) is True
    assumptions = assumptions_for(prof, asked=[])
    assert any("tạm" in a.lower() for a in assumptions)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_clarify.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/dialogue/clarify.py`

```python
from __future__ import annotations
from app.schemas import NeedProfile, SlotQuestion
from app.catalog.category_config import config_for, SlotSpec

MAX_QUESTIONS = 3


def _declined(profile: NeedProfile) -> bool:
    return bool(profile.constraints.get("_khong_muon_tra_loi"))


def missing_critical_slots(profile: NeedProfile, asked: list[str]) -> list[SlotSpec]:
    if profile.category is None:
        return []
    cfg = config_for(profile.category)
    out = [s for s in cfg.ask_slots
           if s.importance >= 2 and s.maps_to not in profile.constraints and s.slot not in asked]
    return sorted(out, key=lambda s: s.importance, reverse=True)


def next_question(profile: NeedProfile, asked: list[str]) -> SlotQuestion | None:
    if profile.category is None or _declined(profile) or len(asked) >= MAX_QUESTIONS:
        return None
    slots = missing_critical_slots(profile, asked)
    if not slots:
        return None
    s = slots[0]
    return SlotQuestion(slot=s.slot, text=s.question, importance=s.importance)


def should_recommend(profile: NeedProfile, asked: list[str]) -> bool:
    return profile.category is not None and next_question(profile, asked) is None


def assumptions_for(profile: NeedProfile, asked: list[str]) -> list[str]:
    notes: list[str] = []
    if profile.category is None:
        return notes
    cfg = config_for(profile.category)
    for s in cfg.ask_slots:
        if s.importance >= 2 and s.maps_to not in profile.constraints:
            notes.append(f"Em tạm bỏ qua thông tin '{s.slot}' vì mình chưa nói rõ; "
                         f"nếu cần em lọc lại chính xác hơn nhé.")
    return notes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_clarify.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/dialogue/ backend/tests/test_clarify.py
git commit -m "feat: clarification policy (ask one critical question at a time)"
```

---

## PHASE 3 — Retrieval & Ranking (deterministic backbone of the "no-hallucination" story)

### Task 10: Hard filters

**Files:**
- Create: `backend/app/retrieval/__init__.py`, `backend/app/retrieval/filters.py`, `backend/tests/test_filters.py`

**Interfaces:**
- Consumes: `Product`, `NeedProfile`, `category_config`.
- Produces:
  - `apply_hard_filters(products: list[Product], profile: NeedProfile) -> list[Product]` — lọc:
    - Ngân sách: giữ sản phẩm **có giá** (`price.available`) và trong `[budget_min, budget_max]`. Sản phẩm không có giá bị loại khỏi đề xuất (không thể so giá) nhưng đếm riêng.
    - `constraints` số/people: nếu constraint là people `[lo, hi]` và spec là people, giữ sản phẩm có vùng người dùng giao nhau. Constraint số đơn (vd `dung tích`) → giữ trong ±25%.
    - Bỏ qua các key constraint bắt đầu bằng `_` (nội bộ).
  - `count_no_price(products) -> int`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_filters.py`

```python
from app.retrieval.filters import apply_hard_filters
from app.schemas import Product, SourcedValue, NeedProfile


def mk(code, price, people=None):
    specs = {}
    if people is not None:
        specs["Số người sử dụng"] = SourcedValue.of(list(people), "thông số nhà sản xuất")
    return Product(category="Tủ lạnh", category_code=code, model_code="m", sku="s",
                   brand="B", display_name="x",
                   price=SourcedValue.of(price, "catalog") if price else SourcedValue.missing(),
                   original_price=SourcedValue.missing(), sale_price=SourcedValue.missing(),
                   specs=specs, spec_doc="", promo_text=None, raw={})


def test_budget_filter():
    ps = [mk("tu_lanh", 12_000_000), mk("tu_lanh", 25_000_000), mk("tu_lanh", None)]
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000)
    out = apply_hard_filters(ps, prof)
    assert len(out) == 1 and out[0].price.value == 12_000_000


def test_people_constraint_overlap():
    ps = [mk("tu_lanh", 10_000_000, people=(3, 4)), mk("tu_lanh", 10_000_000, people=(1, 2))]
    prof = NeedProfile(category="tu_lanh", constraints={"số người": [4, 5]})
    out = apply_hard_filters(ps, prof)
    assert len(out) == 1 and out[0].specs["Số người sử dụng"].value == [3, 4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_filters.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/retrieval/filters.py`

```python
from __future__ import annotations
from app.schemas import Product, NeedProfile

_PEOPLE_FIELD = "Số người sử dụng"


def count_no_price(products: list[Product]) -> int:
    return sum(1 for p in products if not p.price.available)


def _passes_budget(p: Product, profile: NeedProfile) -> bool:
    if not p.price.available:
        return False
    v = p.price.value
    if profile.budget_max is not None and v > profile.budget_max:
        return False
    if profile.budget_min is not None and v < profile.budget_min:
        return False
    return True


def _passes_constraints(p: Product, profile: NeedProfile) -> bool:
    for key, val in profile.constraints.items():
        if key.startswith("_"):
            continue
        if key == "số người" and isinstance(val, list) and len(val) == 2:
            sv = p.specs.get(_PEOPLE_FIELD)
            if sv and sv.available and isinstance(sv.value, list):
                lo, hi = sv.value
                if hi < val[0] or lo > val[1]:   # không giao nhau
                    return False
        elif isinstance(val, (int, float)):
            num = p.number(key.capitalize()) or p.number(key)
            if num is not None and not (val * 0.75 <= num <= val * 1.25):
                return False
    return True


def apply_hard_filters(products: list[Product], profile: NeedProfile) -> list[Product]:
    return [p for p in products if _passes_budget(p, profile) and _passes_constraints(p, profile)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_filters.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/retrieval/filters.py backend/tests/test_filters.py
git commit -m "feat: hard structured filters (budget, people, numeric constraints)"
```

---

### Task 11: Preference scoring + top-3 diversity + why-not group

**Files:**
- Create: `backend/app/retrieval/scoring.py`, `backend/tests/test_scoring.py`

**Interfaces:**
- Consumes: `Product`, `NeedProfile`, `category_config` (`pref_lexicon`, `exclusion_rules`), `ScoredProduct`, `ExcludedGroup`.
- Produces:
  - `score_products(candidates, profile) -> list[ScoredProduct]` — với mỗi pref trong `profile.prefs` khớp `pref_lexicon`, min-max normalize field trên tập candidate: `direction="min"` → giá trị nhỏ cho điểm cao; `direction="max"` → ngược lại. Cộng `weight*norm` vào `score`, lưu `breakdown[pref]` và thêm pref vào `matched`. Sản phẩm thiếu field → 0 điểm phần đó.
  - `select_top3(scored) -> list[ScoredProduct]` — chọn 3 sản phẩm điểm cao nhưng đa dạng: #1 điểm cao nhất; #2, #3 chọn tham lam theo (điểm cao, khác brand nếu có thể, có chênh giá). Nếu < 3 candidate, trả hết.
  - `why_not_group(all_candidates_before_pref_filter, profile) -> ExcludedGroup | None` — nếu một pref khớp `exclusion_rules`, tạo nhóm bị loại: sản phẩm có `field` trống/None (khi `empty_means_bad`) → `ExcludedGroup(label, reason)`; reason nêu vì sao (vd "vì anh/chị ưu tiên tiết kiệm điện").

- [ ] **Step 1: Write the failing test** — `backend/tests/test_scoring.py`

```python
from app.retrieval.scoring import score_products, select_top3, why_not_group
from app.schemas import Product, SourcedValue, NeedProfile


def mk(brand, price, dien, inverter="Digital Inverter"):
    specs = {
        "Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất") if dien is not None else SourcedValue.missing(),
        "Công nghệ tiết kiệm điện": SourcedValue.of(inverter, "thông số nhà sản xuất") if inverter else SourcedValue.missing(),
        "Dung tích tổng": SourcedValue.of(300, "thông số nhà sản xuất"),
    }
    return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs=specs, spec_doc="", promo_text=None, raw={})


def test_energy_saving_pref_scores_lower_consumption_higher():
    cands = [mk("A", 12_000_000, 300), mk("B", 11_000_000, 400)]
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    scored = score_products(cands, prof)
    top = sorted(scored, key=lambda s: s.score, reverse=True)[0]
    assert top.product.brand == "A"                 # 300 kWh < 400 kWh -> điểm cao hơn
    assert "tiết kiệm điện" in top.matched


def test_select_top3_prefers_brand_diversity():
    cands = [mk("A", 12_000_000, 300), mk("A", 12_500_000, 310), mk("B", 11_000_000, 320), mk("C", 9_000_000, 330)]
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    top3 = select_top3(score_products(cands, prof))
    brands = [s.product.brand for s in top3]
    assert len(top3) == 3 and len(set(brands)) >= 2


def test_why_not_group_for_energy_pref():
    cands = [mk("A", 12_000_000, 300, inverter="Digital Inverter"),
             mk("D", 7_000_000, 500, inverter=None)]     # không inverter
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    grp = why_not_group(cands, prof)
    assert grp is not None and "không inverter" in grp.label
    assert "tiết kiệm điện" in grp.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/retrieval/scoring.py`

```python
from __future__ import annotations
from app.schemas import Product, NeedProfile, ScoredProduct, ExcludedGroup
from app.catalog.category_config import config_for


def _normalize(values: list[float], direction: str) -> dict[int, float]:
    present = [v for v in values if v is not None]
    if not present:
        return {}
    lo, hi = min(present), max(present)
    span = hi - lo
    out = {}
    for i, v in enumerate(values):
        if v is None:
            out[i] = 0.0
        elif span == 0:
            out[i] = 1.0
        else:
            frac = (v - lo) / span
            out[i] = (1 - frac) if direction == "min" else frac
    return out


def score_products(candidates: list[Product], profile: NeedProfile) -> list[ScoredProduct]:
    if not candidates:
        return []
    cfg = config_for(profile.category)
    scored = [ScoredProduct(product=p, score=0.0, breakdown={}, matched=[]) for p in candidates]
    for pref in profile.prefs:
        signals = cfg.pref_lexicon.get(pref)
        if not signals:
            continue
        for sig in signals:
            col = [p.number(sig.field) for p in candidates]
            norm = _normalize(col, sig.direction)
            for i, sp in enumerate(scored):
                contrib = sig.weight * norm.get(i, 0.0)
                if contrib > 0:
                    sp.score += contrib
                    sp.breakdown[pref] = sp.breakdown.get(pref, 0.0) + contrib
                    if pref not in sp.matched:
                        sp.matched.append(pref)
    return scored


def select_top3(scored: list[ScoredProduct]) -> list[ScoredProduct]:
    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    if len(ranked) <= 3:
        return ranked
    chosen = [ranked[0]]
    for cand in ranked[1:]:
        if len(chosen) >= 3:
            break
        brands = {c.product.brand for c in chosen}
        # ưu tiên brand khác để đa dạng; nếu vòng còn ít thì vẫn nhận
        if cand.product.brand not in brands or len(ranked) - ranked.index(cand) <= (3 - len(chosen)):
            chosen.append(cand)
    for cand in ranked[1:]:
        if len(chosen) >= 3:
            break
        if cand not in chosen:
            chosen.append(cand)
    return chosen[:3]


def why_not_group(candidates: list[Product], profile: NeedProfile) -> ExcludedGroup | None:
    cfg = config_for(profile.category)
    for rule in cfg.exclusion_rules:
        if rule.when_pref not in profile.prefs:
            continue
        bad = [p for p in candidates
               if rule.empty_means_bad and (p.specs.get(rule.field) is None or not p.specs[rule.field].available)]
        if bad:
            return ExcludedGroup(
                label=rule.label,
                reason=f"Em không đưa nhóm {rule.label} vào dù có thể rẻ hơn, "
                       f"vì anh/chị ưu tiên {rule.when_pref}.")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_scoring.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/retrieval/scoring.py backend/tests/test_scoring.py
git commit -m "feat: preference scoring, top-3 diversity, why-not exclusion group"
```

---

### Task 12: Semantic re-rank (optional) + retrieval engine

**Files:**
- Create: `backend/app/retrieval/embed.py`, `backend/app/retrieval/engine.py`, `backend/tests/test_engine.py`

**Interfaces:**
- Consumes: `filters`, `scoring`, `ProductStore`, `NeedProfile`, `Recommendation`.
- Produces:
  - `embed.semantic_scores(query: str, products: list[Product]) -> dict[int, float]` — nếu `enable_embeddings=False` hoặc `sentence-transformers` không có → trả `{}` (no-op, graceful). Nếu bật: encode `query` + `spec_doc` mỗi product, cosine, min-max normalize → `{index: score}`. Lazy import, model cached.
  - `RetrievalEngine(store)` với `.recommend(profile) -> Recommendation`:
    1. `cands = store.by_category(profile.category)`
    2. `filtered = apply_hard_filters(cands, profile)`
    3. `scored = score_products(filtered, profile)`; cộng `semantic_scores(query_from_profile, filtered)` (trọng số nhỏ 0.3) nếu bật.
    4. `top3 = select_top3(scored)`
    5. `excluded = why_not_group(filtered, profile)`
    6. trả `Recommendation(top3, excluded, assumptions=profile.assumptions)`.
  - `query_from_profile(profile) -> str` — ghép prefs + demographics thành 1 câu truy vấn semantic.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_engine.py`

```python
from app.retrieval.engine import RetrievalEngine, query_from_profile
from app.catalog.loader import ProductStore
from app.schemas import Product, SourcedValue, NeedProfile


def mk(code, brand, price, dien):
    return Product(category="Tủ lạnh", category_code=code, model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs={"Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất"),
                          "Công nghệ tiết kiệm điện": SourcedValue.of("Inverter", "thông số nhà sản xuất")},
                   spec_doc=f"{brand} inverter", promo_text=None, raw={})


def test_engine_end_to_end_ranks_and_filters():
    store = ProductStore([
        mk("tu_lanh", "A", 12_000_000, 300),
        mk("tu_lanh", "B", 11_000_000, 400),
        mk("tu_lanh", "C", 25_000_000, 250),   # ngoài ngân sách
    ])
    prof = NeedProfile(category="tu_lanh", budget_max=20_000_000, prefs=["tiết kiệm điện"])
    reco = RetrievalEngine(store).recommend(prof)
    brands = [s.product.brand for s in reco.top3]
    assert "C" not in brands                 # bị loại vì ngân sách
    assert reco.top3[0].product.brand == "A" # tiết kiệm điện nhất trong tầm giá


def test_query_from_profile():
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện", "ít ồn"], demographics={"đối tượng": "gia đình"})
    q = query_from_profile(prof)
    assert "tiết kiệm điện" in q and "gia đình" in q
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement embed (graceful)** — `backend/app/retrieval/embed.py`

```python
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
```

- [ ] **Step 4: Implement engine** — `backend/app/retrieval/engine.py`

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_engine.py -v`
Expected: PASS (2 tests) — chạy với `enable_embeddings=False` nên embed là no-op.

- [ ] **Step 6: Commit**

```bash
git add backend/app/retrieval/embed.py backend/app/retrieval/engine.py backend/tests/test_engine.py
git commit -m "feat: retrieval engine (filter+score+why-not) with optional semantic re-rank"
```

---

## PHASE 4 — Advice & Guardrails (the "explain + never hallucinate" layer)

### Task 13: Provenance fact-block builder

**Files:**
- Create: `backend/app/advice/__init__.py`, `backend/app/advice/provenance.py`, `backend/tests/test_provenance.py`

**Interfaces:**
- Consumes: `Product`, `ScoredProduct`, `NeedProfile`, `FactCard`, `FactLine`, `category_config`.
- Produces:
  - `build_fact_card(sp: ScoredProduct, profile) -> FactCard` — cho mỗi đề xuất tạo card "Vì sao em đề xuất máy này?":
    - Line giá: từ `product.price` (label "Giá", value định dạng VND, source "catalog"); nếu có `sale_price` khác `original_price` thêm line khuyến mãi.
    - Line cho mỗi field xuất hiện trong `sp.matched` (thông số quyết định) — value + đơn vị + source "thông số nhà sản xuất".
    - `missing`: danh sách nhãn dữ liệu **không có** phải nói thẳng — luôn gồm `"tồn kho"`, `"đánh giá người dùng (review)"`, `"trả góp"` (dataset không có); cộng field matched nào mà product thiếu.
  - `format_vnd(n: int) -> str` — `14990000 -> "14.990.000đ"`.
  - `FACTS_FOR_LLM(cards) -> str` — serialize cards thành khối text "facts" cấp cho LLM sinh lời (chỉ chứa giá trị đã sourced).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_provenance.py`

```python
from app.advice.provenance import build_fact_card, format_vnd, facts_for_llm
from app.schemas import Product, SourcedValue, ScoredProduct, NeedProfile


def mk():
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code="DK1", sku="DK1",
                brand="Daikin", display_name="Tủ lạnh Daikin Inverter 313",
                price=SourcedValue.of(12_400_000, "catalog", detail="giá khuyến mãi"),
                original_price=SourcedValue.of(12_900_000, "catalog"),
                sale_price=SourcedValue.of(12_400_000, "catalog"),
                specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất", unit="kWh/năm")},
                spec_doc="", promo_text="Miễn phí lắp đặt", raw={})
    return ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 1.0}, matched=["tiết kiệm điện"])


def test_format_vnd():
    assert format_vnd(14990000) == "14.990.000đ"


def test_fact_card_has_sourced_lines_and_missing():
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    card = build_fact_card(mk(), prof)
    labels = [l.label for l in card.lines]
    assert "Giá" in labels
    assert any(l.source == "catalog" for l in card.lines)
    assert any(l.source == "thông số nhà sản xuất" for l in card.lines)
    # dữ liệu không có phải được liệt kê thẳng
    assert "tồn kho" in card.missing
    assert "đánh giá người dùng (review)" in card.missing


def test_facts_for_llm_only_contains_sourced_values():
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    facts = facts_for_llm([build_fact_card(mk(), prof)])
    assert "12.400.000đ" in facts
    assert "300" in facts
    assert "tồn kho" in facts.lower()   # nêu rõ phần chưa có dữ liệu
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_provenance.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/advice/provenance.py`

```python
from __future__ import annotations
from app.schemas import ScoredProduct, NeedProfile, FactCard, FactLine

_ALWAYS_MISSING = ["tồn kho", "đánh giá người dùng (review)", "trả góp"]


def format_vnd(n: int) -> str:
    return f"{n:,}".replace(",", ".") + "đ"


def build_fact_card(sp: ScoredProduct, profile: NeedProfile) -> FactCard:
    p = sp.product
    lines: list[FactLine] = []
    missing: list[str] = []

    if p.price.available:
        detail = p.price.provenance.detail if p.price.provenance else None
        lines.append(FactLine(label="Giá", value=format_vnd(int(p.price.value)),
                              source="catalog" + (f" ({detail})" if detail else "")))
        if (p.sale_price.available and p.original_price.available
                and p.sale_price.value != p.original_price.value):
            lines.append(FactLine(label="Giá gốc", value=format_vnd(int(p.original_price.value)),
                                  source="catalog"))
    else:
        missing.append("giá")

    for field in sp.matched:
        sv = p.specs.get(field)
        # 'field' là pref key; ánh xạ sang thông số thực bằng chính các spec matched
    # liệt kê mọi spec có giá trị liên quan tới prefs đã khớp
    for field, sv in p.specs.items():
        if sv.available and any(field for _ in [0]) and sv.value is not None:
            if _relevant(field, sp):
                unit = f" {sv.unit}" if sv.unit else ""
                lines.append(FactLine(label=field, value=f"{sv.value}{unit}",
                                      source=sv.provenance.source if sv.provenance else "thông số nhà sản xuất"))

    missing.extend(_ALWAYS_MISSING)
    return FactCard(title=f"Vì sao em đề xuất {p.display_name}?", lines=lines, missing=missing)


def _relevant(field: str, sp: ScoredProduct) -> bool:
    # spec được coi là "quyết định" nếu nó là field đứng sau một pref đã khớp
    from app.catalog.category_config import config_for
    cfg = config_for(sp.product.category_code)
    fields = set()
    for pref in sp.matched:
        for sig in cfg.pref_lexicon.get(pref, []):
            fields.add(sig.field)
    return field in fields


def facts_for_llm(cards: list[FactCard]) -> str:
    blocks = []
    for c in cards:
        rows = [f"  - {l.label}: {l.value}  [nguồn: {l.source}]" for l in c.lines]
        miss = ", ".join(c.missing)
        blocks.append(c.title + "\n" + "\n".join(rows) + f"\n  - CHƯA CÓ DỮ LIỆU: {miss}")
    return "\n\n".join(blocks)
```

> Lưu ý cho người triển khai: hàm `build_fact_card` ở trên có một vòng lặp thừa (`for field in sp.matched`) — xoá nó, chỉ giữ vòng `for field, sv in p.specs.items()` với `_relevant`. Rút gọn điều kiện thành `if sv.available and sv.value is not None and _relevant(field, sp)`.

- [ ] **Step 4: Dọn implementation cho gọn** — thay thân `build_fact_card` phần spec bằng:

```python
    for field, sv in p.specs.items():
        if sv.available and sv.value is not None and _relevant(field, sp):
            unit = f" {sv.unit}" if sv.unit else ""
            lines.append(FactLine(label=field, value=f"{sv.value}{unit}",
                                  source=sv.provenance.source if sv.provenance else "thông số nhà sản xuất"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_provenance.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/advice/provenance.py backend/tests/test_provenance.py
git commit -m "feat: provenance fact-card builder + explicit missing-data disclosure"
```

---

### Task 14: LLM explanation generation (grounded)

**Files:**
- Create: `backend/app/advice/generate.py`, `backend/tests/test_generate.py`

**Interfaces:**
- Consumes: `LLMClient`, `Recommendation`, `provenance.facts_for_llm/build_fact_card`, `AdviceResult`.
- Produces:
  - `ADVICE_SYSTEM_PROMPT: str` — chỉ dùng facts được cấp; ngôn ngữ bình dân, không thuật ngữ marketing; nêu trade-off từng máy; giải thích cả nhóm bị loại; mọi con số phải đến từ facts; thiếu → nói "chưa có dữ liệu".
  - `generate_advice(reco: Recommendation, profile, llm) -> AdviceResult` — build cards (Task 13), gọi `llm.complete_text` với facts + prefs, trả `AdviceResult(message, cards, assumptions, warnings=[])`. Nếu `top3` rỗng → message cố định "Em chưa tìm được máy khớp tiêu chí trong tầm giá này..." (không gọi LLM).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_generate.py`

```python
from app.llm.client import FakeLLM
from app.advice.generate import generate_advice
from app.schemas import Product, SourcedValue, ScoredProduct, Recommendation, NeedProfile, ExcludedGroup


def sp(brand, price):
    p = Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                brand=brand, display_name=f"Tủ lạnh {brand}",
                price=SourcedValue.of(price, "catalog"),
                original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất", unit="kWh/năm")},
                spec_doc="", promo_text=None, raw={})
    return ScoredProduct(product=p, score=1.0, breakdown={"tiết kiệm điện": 1.0}, matched=["tiết kiệm điện"])


def test_generate_advice_builds_cards_and_message():
    reco = Recommendation(top3=[sp("Daikin", 12_400_000), sp("Panasonic", 11_500_000)],
                          excluded=ExcludedGroup(label="máy non-inverter", reason="vì ưu tiên tiết kiệm điện"),
                          assumptions=["Em tạm tính phòng không nắng."])
    prof = NeedProfile(category="tu_lanh", prefs=["tiết kiệm điện"])
    fake = FakeLLM(text_responses=["Với nhu cầu tiết kiệm điện, em đề xuất 2 máy..."])
    result = generate_advice(reco, prof, fake)
    assert "đề xuất" in result.message
    assert len(result.cards) == 2
    assert result.assumptions == ["Em tạm tính phòng không nắng."]
    # facts phải được đưa vào prompt gửi LLM
    sys, user = fake.calls[0]
    assert "12.400.000đ" in user and "tồn kho" in user.lower()


def test_generate_advice_empty_top3_no_llm_call():
    reco = Recommendation(top3=[], excluded=None, assumptions=[])
    prof = NeedProfile(category="tu_lanh")
    fake = FakeLLM(text_responses=["should not be used"])
    result = generate_advice(reco, prof, fake)
    assert "chưa tìm được" in result.message.lower()
    assert fake.calls == []          # không gọi LLM khi rỗng
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_generate.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/advice/generate.py`

```python
from __future__ import annotations
from app.schemas import Recommendation, NeedProfile, AdviceResult
from app.llm.client import LLMClient
from app.advice.provenance import build_fact_card, facts_for_llm

ADVICE_SYSTEM_PROMPT = (
    "Bạn là nhân viên tư vấn điện máy thân thiện, nói tiếng Việt bình dân (không dùng thuật ngữ "
    "marketing). Bạn CHỈ được dùng các con số và dữ kiện trong phần FACTS bên dưới; "
    "TUYỆT ĐỐI không bịa thêm giá, thông số, khuyến mãi, tồn kho. Nếu một thông tin nằm trong mục "
    "'CHƯA CÓ DỮ LIỆU', hãy nói thẳng 'em chưa có dữ liệu' về mục đó. "
    "Trình bày: mở đầu 1 câu, rồi liệt kê từng máy kèm 1 điểm mạnh và 1 điểm đánh đổi thật, "
    "cuối cùng giải thích ngắn gọn vì sao có nhóm sản phẩm em không đề xuất (nếu có). "
    "Giữ giọng gần gũi, không phóng đại, không ép mua."
)


def _empty_message() -> str:
    return ("Dạ em chưa tìm được máy khớp tiêu chí trong tầm giá này. "
            "Anh/chị có thể nới ngân sách hoặc bớt một ràng buộc để em tìm lại nhé?")


def generate_advice(reco: Recommendation, profile: NeedProfile, llm: LLMClient) -> AdviceResult:
    if not reco.top3:
        return AdviceResult(message=_empty_message(), cards=[], assumptions=reco.assumptions, warnings=[])

    cards = [build_fact_card(sp, profile) for sp in reco.top3]
    facts = facts_for_llm(cards)
    excluded_txt = f"\nNhóm không đề xuất: {reco.excluded.label} — {reco.excluded.reason}" if reco.excluded else ""
    prefs_txt = ", ".join(profile.prefs) or "không nêu rõ"
    user = (f"Nhu cầu khách: ưu tiên {prefs_txt}.\n\nFACTS:\n{facts}{excluded_txt}\n\n"
            "Viết lời tư vấn theo đúng quy tắc.")
    message = llm.complete_text(ADVICE_SYSTEM_PROMPT, user)
    return AdviceResult(message=message, cards=cards, assumptions=reco.assumptions, warnings=[])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_generate.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/advice/generate.py backend/tests/test_generate.py
git commit -m "feat: grounded LLM advice generation from sourced facts"
```

---

### Task 15: Number-grounding guardrail (verifier)

**Files:**
- Create: `backend/app/advice/verify.py`, `backend/tests/test_verify.py`

**Interfaces:**
- Consumes: `AdviceResult`, `FactCard`.
- Produces:
  - `extract_numbers(text: str) -> list[str]` — bóc mọi token số có nghĩa (giá `"12.400.000đ"`, số kèm đơn vị `"300 kWh"`, `"19dB"`, năm bỏ qua nếu muốn). Chuẩn hoá về chuỗi số thuần để so.
  - `allowed_numbers(cards: list[FactCard]) -> set[str]` — tập số hợp lệ trích từ các FactLine.value.
  - `verify_advice(result: AdviceResult) -> AdviceResult` — tìm số trong `result.message` không nằm trong `allowed_numbers`; nếu có → thêm cảnh báo vào `result.warnings` (`"Số chưa truy được nguồn: X"`) để orchestrator xử lý (không tự sửa message). Trả result đã gắn warnings.
  - `is_grounded(result) -> bool` — True nếu không có warning số lạ.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_verify.py`

```python
from app.advice.verify import extract_numbers, allowed_numbers, verify_advice, is_grounded
from app.schemas import AdviceResult, FactCard, FactLine


def cards():
    return [FactCard(title="t", lines=[
        FactLine(label="Giá", value="12.400.000đ", source="catalog"),
        FactLine(label="Điện năng tiêu thụ", value="300 kWh/năm", source="thông số nhà sản xuất"),
    ], missing=["tồn kho"])]


def test_extract_and_allowed():
    nums = allowed_numbers(cards())
    assert "12400000" in nums and "300" in nums


def test_verify_flags_ungrounded_number():
    res = AdviceResult(message="Máy này chỉ 9.990.000đ, tiết kiệm 300 kWh.", cards=cards())
    out = verify_advice(res)
    assert not is_grounded(out)
    assert any("9990000" in w for w in out.warnings)   # 9.990.000 không có nguồn


def test_verify_passes_when_all_grounded():
    res = AdviceResult(message="Máy này giá 12.400.000đ, điện 300 kWh/năm.", cards=cards())
    out = verify_advice(res)
    assert is_grounded(out) and out.warnings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_verify.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/advice/verify.py`

```python
from __future__ import annotations
import re
from app.schemas import AdviceResult, FactCard

# số có ít nhất 2 chữ số, cho phép dấu . , phân tách nghìn / thập phân
_NUM = re.compile(r"\d[\d.,]*\d")


def _canon(token: str) -> str:
    # bỏ mọi dấu . , -> chuỗi chữ số thuần (12.400.000 -> 12400000; 1,3 -> 13)
    return re.sub(r"[.,]", "", token)


def extract_numbers(text: str) -> list[str]:
    return [_canon(m.group(0)) for m in _NUM.finditer(text)]


def allowed_numbers(cards: list[FactCard]) -> set[str]:
    allowed: set[str] = set()
    for c in cards:
        for l in c.lines:
            for n in extract_numbers(l.value):
                allowed.add(n)
    return allowed


# các số "an toàn" (đời thường) không cần nguồn: 1..9 chữ số đơn, phần trăm nhỏ
_SAFE = {str(i) for i in range(0, 100)}


def verify_advice(result: AdviceResult) -> AdviceResult:
    allowed = allowed_numbers(result.cards)
    warnings = list(result.warnings)
    for n in extract_numbers(result.message):
        if n in allowed or n in _SAFE:
            continue
        # cho phép khớp một phần (vd '12400000' xuất hiện khác định dạng)
        if any(n in a or a in n for a in allowed):
            continue
        warnings.append(f"Số chưa truy được nguồn: {n}")
    result.warnings = warnings
    return result


def is_grounded(result: AdviceResult) -> bool:
    return not any(w.startswith("Số chưa truy được nguồn") for w in result.warnings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_verify.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/advice/verify.py backend/tests/test_verify.py
git commit -m "feat: number-grounding guardrail flags ungrounded figures"
```

---

### Task 16: Budget up/down advice

**Files:**
- Create: `backend/app/advice/budget.py`, `backend/tests/test_budget.py`

**Interfaces:**
- Consumes: `ProductStore`, `NeedProfile`, `RetrievalEngine`, `ScoredProduct`.
- Produces:
  - `budget_alternatives(profile, store, direction: Literal["down","up"]) -> list[ScoredProduct]` — `down`: bỏ ràng buộc `budget_min`, hạ trần xuống ~70% giá đề xuất hiện tại (hoặc dùng `budget_max*0.7`) → chạy engine, trả top rẻ hơn. `up`: nâng trần lên ~1.4×, trả lựa chọn cao cấp hơn. Luôn chỉ dùng sản phẩm cùng `category`.
  - `describe_tradeoff(cheaper: ScoredProduct, current_price: int) -> str` — câu mô tả đánh đổi quy đổi được (chênh giá VND). Không bịa con số tiền điện; nếu không có dữ liệu điện thì chỉ nói chênh giá.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_budget.py`

```python
from app.advice.budget import budget_alternatives, describe_tradeoff
from app.catalog.loader import ProductStore
from app.schemas import Product, SourcedValue, NeedProfile, ScoredProduct


def mk(brand, price):
    return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs={"Điện năng tiêu thụ": SourcedValue.of(350, "thông số nhà sản xuất")},
                   spec_doc="", promo_text=None, raw={})


def test_budget_down_returns_cheaper():
    store = ProductStore([mk("A", 12_000_000), mk("B", 8_900_000), mk("C", 7_500_000)])
    prof = NeedProfile(category="tu_lanh", budget_max=15_000_000, prefs=[])
    alts = budget_alternatives(prof, store, direction="down")
    assert alts and all(a.product.price.value <= 12_000_000 for a in alts)
    assert any(a.product.price.value <= 8_900_000 for a in alts)


def test_describe_tradeoff_price_delta():
    cheaper = ScoredProduct(product=mk("B", 8_900_000), score=0.0)
    txt = describe_tradeoff(cheaper, current_price=12_400_000)
    assert "3.500.000đ" in txt   # 12.4tr - 8.9tr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/advice/budget.py`

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_budget.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/advice/budget.py backend/tests/test_budget.py
git commit -m "feat: budget up/down alternatives with quantified trade-off"
```

---

## PHASE 5 — Orchestration & API

### Task 17: Turn orchestrator (state machine)

**Files:**
- Create: `backend/app/orchestrator.py`, `backend/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `parse_need`, `clarify`, `RetrievalEngine`, `generate_advice`, `verify_advice`, `ProductStore`, `LLMClient`.
- Produces:
  - `ChatState(profile: NeedProfile, asked: list[str], stage: str)` (pydantic; `stage ∈ {"collecting","recommended"}`).
  - `TurnResult(reply: str, stage: str, question: str | None, advice: AdviceResult | None, need: NeedProfile)`.
  - `Orchestrator(store, llm)` với `.handle_turn(state: ChatState, message: str) -> tuple[ChatState, TurnResult]`:
    1. `state.profile = parse_need(message, llm, prior=state.profile)`
    2. Nếu `category is None` → reply hỏi khách muốn mua nhóm hàng gì (không đoán).
    3. Nếu `next_question` có → reply = câu hỏi, ghi `asked`, stage "collecting".
    4. Nếu `should_recommend` → chèn `assumptions_for` vào profile, `engine.recommend`, `generate_advice`, `verify_advice`; stage "recommended"; reply = advice.message (+ ghi chú cảnh báo nếu có số chưa grounded → thay bằng câu an toàn).
  - Guardrail tại orchestrator: nếu `not is_grounded(advice)` → KHÔNG hiển thị message LLM; thay bằng bản tóm tắt deterministic từ cards (an toàn tuyệt đối). Đây là "fail closed".

- [ ] **Step 1: Write the failing test** — `backend/tests/test_orchestrator.py`

```python
from app.orchestrator import Orchestrator, ChatState
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue, NeedProfile


def mk(brand, price, dien):
    return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                   brand=brand, display_name=f"Tủ lạnh {brand}",
                   price=SourcedValue.of(price, "catalog"),
                   original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                   specs={"Điện năng tiêu thụ": SourcedValue.of(dien, "thông số nhà sản xuất", unit="kWh/năm"),
                          "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất"),
                          "Công nghệ tiết kiệm điện": SourcedValue.of("Inverter", "thông số nhà sản xuất")},
                   spec_doc="inverter", promo_text=None, raw={})


def store():
    return ProductStore([mk("A", 12_000_000, 300), mk("B", 11_000_000, 400), mk("C", 9_000_000, 380)])


def test_asks_when_missing_critical_slot():
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "prefs"]}])
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "mua tu lanh duoi 20tr tiet kiem dien")
    assert res.question is not None and res.stage == "collecting"
    assert "người" in res.question.lower()


def test_recommends_when_enough_info_and_grounded():
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000, "constraints": {"số người": [3, 4]},
                         "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "constraints", "prefs"]}],
        text_responses=["Với gia đình 3-4 người ưu tiên tiết kiệm điện, em gợi ý các máy có giá 12.000.000đ và 11.000.000đ."])
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "nha 4 nguoi, tu lanh duoi 20tr, tiet kiem dien")
    assert res.stage == "recommended" and res.advice is not None
    assert len(res.advice.top3) if hasattr(res.advice, "top3") else True
    assert res.advice.cards


def test_fail_closed_when_llm_hallucinates_number():
    llm = FakeLLM(
        json_responses=[{"category": "tu_lanh", "budget_max": 20000000, "constraints": {"số người": [3, 4]},
                         "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "constraints", "prefs"]}],
        text_responses=["Máy này chỉ 999.999đ, quá rẻ!"])   # số bịa
    orch = Orchestrator(store(), llm)
    state = ChatState(profile=NeedProfile(), asked=[], stage="collecting")
    state, res = orch.handle_turn(state, "nha 4 nguoi, tu lanh duoi 20tr, tiet kiem dien")
    assert "999.999" not in res.reply     # message LLM bịa bị chặn
    assert res.advice is not None          # vẫn có đề xuất (bản deterministic an toàn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** — `backend/app/orchestrator.py`

```python
from __future__ import annotations
from pydantic import BaseModel, Field
from app.schemas import NeedProfile, AdviceResult
from app.llm.client import LLMClient
from app.catalog.loader import ProductStore
from app.nlu.parser import parse_need
from app.dialogue.clarify import next_question, should_recommend, assumptions_for
from app.retrieval.engine import RetrievalEngine
from app.advice.generate import generate_advice
from app.advice.verify import verify_advice, is_grounded
from app.advice.provenance import format_vnd


class ChatState(BaseModel):
    profile: NeedProfile = Field(default_factory=NeedProfile)
    asked: list[str] = Field(default_factory=list)
    stage: str = "collecting"


class TurnResult(BaseModel):
    reply: str
    stage: str
    question: str | None = None
    advice: AdviceResult | None = None
    need: NeedProfile


def _safe_summary(advice: AdviceResult) -> str:
    # bản tóm tắt deterministic từ cards (không dùng chữ LLM) khi guardrail bật
    lines = ["Dạ em gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(advice.cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        title = c.title.replace("Vì sao em đề xuất ", "").rstrip("?")
        lines.append(f"{i}. {title} — giá {price}.")
    return "\n".join(lines)


class Orchestrator:
    def __init__(self, store: ProductStore, llm: LLMClient):
        self.store = store
        self.llm = llm
        self.engine = RetrievalEngine(store)

    def handle_turn(self, state: ChatState, message: str):
        state.profile = parse_need(message, self.llm, prior=state.profile)

        if state.profile.category is None:
            res = TurnResult(reply="Dạ anh/chị đang muốn tìm nhóm sản phẩm nào ạ "
                                   "(tủ lạnh, máy giặt sấy, máy rửa chén, tủ đông, đồng hồ thông minh, màn hình)?",
                             stage="collecting", need=state.profile)
            return state, res

        q = next_question(state.profile, state.asked)
        if q is not None:
            state.asked.append(q.slot)
            return state, TurnResult(reply=q.text, stage="collecting", question=q.text, need=state.profile)

        if should_recommend(state.profile, state.asked):
            for a in assumptions_for(state.profile, state.asked):
                if a not in state.profile.assumptions:
                    state.profile.assumptions.append(a)
            reco = self.engine.recommend(state.profile)
            advice = verify_advice(generate_advice(reco, state.profile, self.llm))
            # gắn top3 vào advice để API tiện dùng (qua warnings? -> thêm field động không có; giữ ở TurnResult)
            state.stage = "recommended"
            reply = advice.message if is_grounded(advice) else _safe_summary(advice)
            if advice.assumptions:
                reply += "\n\n(" + " ".join(advice.assumptions) + ")"
            res = TurnResult(reply=reply, stage="recommended", advice=advice, need=state.profile)
            return state, res

        return state, TurnResult(reply="Dạ anh/chị cho em thêm chút thông tin nhé.",
                                 stage="collecting", need=state.profile)
```

> Ghi chú: test `test_recommends...` truy cập `res.advice.top3` phòng hờ — `AdviceResult` không có `top3`; sửa test cho khớp (chỉ assert `res.advice.cards`). Đảm bảo test cuối kiểm tra `res.reply` không chứa số bịa và `res.advice is not None`.

- [ ] **Step 4: Sửa test cho khớp interface** (bỏ dòng `top3` thừa trong `test_recommends...`, chỉ giữ `assert res.advice.cards`).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_orchestrator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Full backend suite green**

Run: `cd backend && ./.venv/Scripts/pytest -q`
Expected: tất cả test từ Task 1–17 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/orchestrator.py backend/tests/test_orchestrator.py
git commit -m "feat: turn orchestrator state machine with fail-closed guardrail"
```

---

### Task 18: FastAPI app + session store + endpoints

**Files:**
- Create: `backend/app/main.py`, `backend/app/session.py`, `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `Orchestrator`, `ChatState`, `get_store`, `get_llm`.
- Produces:
  - `SessionStore` (in-memory dict `session_id -> ChatState`, có `mask`-logging cho message).
  - FastAPI app với:
    - `POST /api/chat` body `{session_id: str, message: str}` → `{reply, stage, question, need, recommendation}` (recommendation = cards + assumptions + warnings serialized nếu stage recommended).
    - `POST /api/reset` body `{session_id}` → xoá state.
    - `GET /api/health` → `{status:"ok", products: <count>}`.
  - Dependency injection: `get_orchestrator()` dùng `get_store()` + `get_llm()`; test override bằng `FakeLLM` + store nhỏ.
  - CORS mở cho `http://localhost:5173` (Vite dev).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_api.py`

```python
from fastapi.testclient import TestClient
from app.main import app, get_orchestrator
from app.orchestrator import Orchestrator
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue


def _store():
    def mk(brand, price):
        return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                       brand=brand, display_name=f"Tủ lạnh {brand}",
                       price=SourcedValue.of(price, "catalog"),
                       original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                       specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất"),
                              "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất")},
                       spec_doc="", promo_text=None, raw={})
    return ProductStore([mk("A", 12_000_000), mk("B", 11_000_000)])


def _fake_orch():
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "constraints": {"số người": [3, 4]}, "prefs": ["tiết kiệm điện"],
                                   "known": ["category", "budget_max", "constraints", "prefs"]}],
                  text_responses=["Em gợi ý máy giá 12.000.000đ và 11.000.000đ."])
    return Orchestrator(_store(), llm)


def test_health():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_chat_recommends():
    app.dependency_overrides[get_orchestrator] = _fake_orch
    client = TestClient(app)
    r = client.post("/api/chat", json={"session_id": "s1", "message": "nha 4 nguoi mua tu lanh 20tr tiet kiem dien"})
    body = r.json()
    assert r.status_code == 200
    assert body["stage"] == "recommended"
    assert body["recommendation"]["cards"]
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Implement session store** — `backend/app/session.py`

```python
from __future__ import annotations
from app.orchestrator import ChatState


class SessionStore:
    def __init__(self):
        self._states: dict[str, ChatState] = {}

    def get(self, sid: str) -> ChatState:
        return self._states.setdefault(sid, ChatState())

    def set(self, sid: str, state: ChatState) -> None:
        self._states[sid] = state

    def reset(self, sid: str) -> None:
        self._states.pop(sid, None)


SESSIONS = SessionStore()


def mask(text: str) -> str:
    # không log nội dung khách; chỉ log độ dài (bảo mật PII)
    return f"<{len(text)} chars>"
```

- [ ] **Step 4: Implement app** — `backend/app/main.py`

```python
from __future__ import annotations
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app.orchestrator import Orchestrator
from app.catalog.loader import get_store
from app.llm.client import get_llm
from app.session import SESSIONS

app = FastAPI(title="Trợ lý AI Điện Máy Xanh")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


class ChatIn(BaseModel):
    session_id: str
    message: str


class ResetIn(BaseModel):
    session_id: str


def get_orchestrator() -> Orchestrator:
    return Orchestrator(get_store(), get_llm())


@app.get("/api/health")
def health():
    try:
        n = len(get_store().all())
    except Exception:
        n = 0
    return {"status": "ok", "products": n}


@app.post("/api/chat")
def chat(body: ChatIn, orch: Orchestrator = Depends(get_orchestrator)):
    state = SESSIONS.get(body.session_id)
    state, result = orch.handle_turn(state, body.message)
    SESSIONS.set(body.session_id, state)
    recommendation = None
    if result.advice is not None:
        recommendation = {
            "cards": [c.model_dump() for c in result.advice.cards],
            "assumptions": result.advice.assumptions,
            "warnings": result.advice.warnings,
        }
    return {"reply": result.reply, "stage": result.stage,
            "question": result.question, "need": result.need.model_dump(),
            "recommendation": recommendation}


@app.post("/api/reset")
def reset(body: ResetIn):
    SESSIONS.reset(body.session_id)
    return {"status": "reset"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Chạy server thật + kiểm tra sức khoẻ (thủ công)**

```bash
cd backend && ./.venv/Scripts/uvicorn app.main:app --reload --port 8000
```
Ở terminal khác: `curl http://localhost:8000/api/health` → `{"status":"ok","products":3960}` (cần đã build catalog ở Task 6).

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/app/session.py backend/tests/test_api.py
git commit -m "feat: FastAPI chat API with in-memory sessions + PII-safe logging"
```

---

## PHASE 6 — Frontend (React + Vite)

### Task 19: Chat UI + "Vì sao em đề xuất máy này?" source panel

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.js`, `frontend/index.html`, `frontend/src/main.jsx`, `frontend/src/api.js`, `frontend/src/App.jsx`, `frontend/src/components/Message.jsx`, `frontend/src/components/SourcePanel.jsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: backend `POST /api/chat`, `POST /api/reset`, `GET /api/health`.
- Produces: SPA chat: khung hội thoại, gửi tin nhắn, hiển thị `reply`; khi `recommendation` có → render danh sách card top-3, mỗi card có nút "Vì sao em đề xuất máy này?" mở `SourcePanel` (liệt kê từng `FactLine` value + `[nguồn: ...]` + mục "Chưa có dữ liệu"). Hiển thị `warnings` (nếu có) bằng banner cảnh báo.

> Đây là task UI: TDD theo unit không phù hợp; "test" là chạy app thật và quan sát. Vẫn viết code đầy đủ, mỗi step là một file hoàn chỉnh, rồi verify bằng smoke test ở Step cuối.

- [ ] **Step 1: Scaffold Vite React**

`frontend/package.json`:
```json
{
  "name": "emx-advisor-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.1", "vite": "^5.4.0" }
}
```
`frontend/vite.config.js`:
```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000' } },
})
```
`frontend/index.html`:
```html
<!doctype html>
<html lang="vi">
  <head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Trợ lý AI Điện Máy Xanh</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body>
</html>
```
Run:
```bash
cd frontend && npm install
```

- [ ] **Step 2: API client** — `frontend/src/api.js`

```js
export async function sendChat(sessionId, message) {
  const r = await fetch('/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!r.ok) throw new Error('API error')
  return r.json()
}
export async function resetChat(sessionId) {
  await fetch('/api/reset', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
}
```

- [ ] **Step 3: Source panel** — `frontend/src/components/SourcePanel.jsx`

```jsx
import { useState } from 'react'

export default function SourcePanel({ card }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="source">
      <button className="why-btn" onClick={() => setOpen(!open)}>
        {open ? '▲ Ẩn nguồn' : '▼ Vì sao em đề xuất máy này?'}
      </button>
      {open && (
        <div className="source-body">
          <ul>
            {card.lines.map((l, i) => (
              <li key={i}><b>{l.label}:</b> {l.value} <span className="src">[nguồn: {l.source}]</span></li>
            ))}
          </ul>
          {card.missing?.length > 0 && (
            <p className="missing">Chưa có dữ liệu: {card.missing.join(', ')}.</p>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Message component** — `frontend/src/components/Message.jsx`

```jsx
import SourcePanel from './SourcePanel'

export default function Message({ msg }) {
  const { role, text, recommendation } = msg
  return (
    <div className={`msg ${role}`}>
      <div className="bubble" style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
      {recommendation?.warnings?.length > 0 && (
        <div className="warn">⚠ Có số liệu chưa truy được nguồn — đã ẩn để tránh sai lệch.</div>
      )}
      {recommendation?.cards?.map((c, i) => (
        <div className="card" key={i}>
          <div className="card-title">{c.title.replace('Vì sao em đề xuất ', '').replace('?', '')}</div>
          <SourcePanel card={c} />
        </div>
      ))}
      {recommendation?.assumptions?.length > 0 && (
        <div className="assume">Giả định: {recommendation.assumptions.join(' ')}</div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: App** — `frontend/src/App.jsx`

```jsx
import { useState } from 'react'
import { sendChat, resetChat } from './api'
import Message from './components/Message'

const SID = 'demo-' + Math.random().toString(36).slice(2)

export default function App() {
  const [messages, setMessages] = useState([
    { role: 'bot', text: 'Dạ em là trợ lý Điện Máy Xanh. Anh/chị cần tư vấn sản phẩm gì ạ?' },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setMessages((m) => [...m, { role: 'user', text }])
    setInput(''); setBusy(true)
    try {
      const res = await sendChat(SID, text)
      setMessages((m) => [...m, { role: 'bot', text: res.reply, recommendation: res.recommendation }])
    } catch {
      setMessages((m) => [...m, { role: 'bot', text: 'Xin lỗi, hệ thống đang bận. Anh/chị thử lại nhé.' }])
    } finally { setBusy(false) }
  }

  async function onReset() {
    await resetChat(SID)
    setMessages([{ role: 'bot', text: 'Đã làm mới. Anh/chị cần tư vấn gì ạ?' }])
  }

  return (
    <div className="app">
      <header><h1>Trợ lý AI Điện Máy Xanh</h1><button onClick={onReset}>Làm mới</button></header>
      <div className="chat">{messages.map((m, i) => <Message key={i} msg={m} />)}</div>
      <form className="composer" onSubmit={submit}>
        <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
               placeholder="VD: mua tu lanh duoi 20tr cho nha 4 nguoi, tiet kiem dien" />
        <button disabled={busy}>{busy ? '...' : 'Gửi'}</button>
      </form>
    </div>
  )
}
```

- [ ] **Step 6: main + styles** — `frontend/src/main.jsx`

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
ReactDOM.createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)
```
`frontend/src/styles.css`:
```css
* { box-sizing: border-box; font-family: system-ui, sans-serif; }
body { margin: 0; background: #f5f6f8; }
.app { max-width: 720px; margin: 0 auto; height: 100vh; display: flex; flex-direction: column; }
header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: #1a7a3a; color: #fff; }
header h1 { font-size: 18px; margin: 0; }
header button { background: #fff; color: #1a7a3a; border: 0; border-radius: 6px; padding: 6px 10px; cursor: pointer; }
.chat { flex: 1; overflow-y: auto; padding: 16px; }
.msg { margin-bottom: 14px; }
.msg.user { text-align: right; }
.bubble { display: inline-block; padding: 10px 14px; border-radius: 12px; background: #fff; max-width: 90%; text-align: left; }
.msg.user .bubble { background: #d9f0e1; }
.card { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 10px 12px; margin-top: 8px; }
.card-title { font-weight: 600; }
.why-btn { background: none; border: 0; color: #1a7a3a; cursor: pointer; padding: 4px 0; }
.source-body { font-size: 14px; }
.src { color: #888; }
.missing { color: #b26a00; }
.warn { color: #b00020; font-size: 14px; margin-top: 6px; }
.assume { color: #555; font-size: 13px; margin-top: 6px; font-style: italic; }
.composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid #e0e0e0; background: #fff; }
.composer input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
.composer button { padding: 10px 18px; background: #1a7a3a; color: #fff; border: 0; border-radius: 8px; cursor: pointer; }
```

- [ ] **Step 7: Smoke test (thủ công, đủ điều kiện demo)**

Terminal 1: `cd backend && ./.venv/Scripts/uvicorn app.main:app --port 8000`
Terminal 2: `cd frontend && npm run dev` → mở `http://localhost:5173`.
Gõ: `mua tu lanh duoi 20tr cho nha 4 nguoi, tiet kiem dien, it on`.
Expected: bot hỏi lại (nếu thiếu slot) → sau khi đủ, hiện top-3 card, bấm "Vì sao..." thấy nguồn từng con số + mục "Chưa có dữ liệu: tồn kho, review, trả góp".

- [ ] **Step 8: Commit**

```bash
git add frontend/
git commit -m "feat: React chat UI with per-recommendation source panel"
```

---

## PHASE 7 — Evaluation & Docs (deliverables + judging metrics)

### Task 20: Scenario eval harness (need-accuracy + hallucination rate)

**Files:**
- Create: `backend/eval/scenarios.jsonl`, `backend/eval/run_eval.py`, `backend/tests/test_eval.py`

**Interfaces:**
- Consumes: `parse_need`, `RetrievalEngine`, `generate_advice`, `verify_advice`, `ProductStore`, `LLMClient`.
- Produces:
  - `scenarios.jsonl` — mỗi dòng: `{"message": <câu khách>, "expect_category": <code>, "expect_budget_max": <int|null>, "expect_prefs": [<pref>...]}`; tối thiểu 15 tình huống phủ 6 category, có câu không dấu/viết tắt, và 2 câu "cứ gợi ý đại".
  - `evaluate(scenarios, llm, store) -> dict` — trả metrics: `category_acc`, `budget_acc`, `pref_recall`, `hallucination_rate` (tỉ lệ câu trả lời có warning số chưa grounded trên các case sinh đề xuất). Dùng LLM thật khi chạy CLI; test dùng `FakeLLM` + subset.
  - CLI: `python eval/run_eval.py` in bảng metrics.

- [ ] **Step 1: Tạo bộ scenarios** — `backend/eval/scenarios.jsonl` (15+ dòng; ví dụ mẫu, người triển khai bổ sung cho đủ 6 category)

```json
{"message": "e muon mua tu lanh duoi 20tr cho nha 4 nguoi, tiet kiem dien", "expect_category": "tu_lanh", "expect_budget_max": 20000000, "expect_prefs": ["tiết kiệm điện"]}
{"message": "can cai dong ho thong minh cho be di hoc, pin lau", "expect_category": "dong_ho", "expect_budget_max": null, "expect_prefs": ["pin lâu"]}
{"message": "mua man hinh gaming duoi 5 trieu phan hoi nhanh", "expect_category": "man_hinh", "expect_budget_max": 5000000, "expect_prefs": ["phản hồi nhanh"]}
{"message": "may rua chen it on cho gia dinh 4 nguoi", "expect_category": "may_rua_chen", "expect_budget_max": null, "expect_prefs": ["ít ồn"]}
{"message": "tu dong tiet kiem dien dung tich lon", "expect_category": "tu_mat", "expect_budget_max": null, "expect_prefs": ["tiết kiệm điện", "dung tích lớn"]}
{"message": "tu lanh gia re duoi 8tr cu goi y dai di em", "expect_category": "tu_lanh", "expect_budget_max": 8000000, "expect_prefs": []}
```

- [ ] **Step 2: Write the failing test** — `backend/tests/test_eval.py`

```python
from app.eval_utils import evaluate  # sẽ tạo ở Step 3 (module dùng chung)
from app.catalog.loader import ProductStore
from app.llm.client import FakeLLM
from app.schemas import Product, SourcedValue


def _store():
    def mk(brand, price):
        return Product(category="Tủ lạnh", category_code="tu_lanh", model_code=brand, sku=brand,
                       brand=brand, display_name=f"Tủ lạnh {brand}",
                       price=SourcedValue.of(price, "catalog"),
                       original_price=SourcedValue.of(price, "catalog"), sale_price=SourcedValue.missing(),
                       specs={"Điện năng tiêu thụ": SourcedValue.of(300, "thông số nhà sản xuất"),
                              "Số người sử dụng": SourcedValue.of([3, 4], "thông số nhà sản xuất")},
                       spec_doc="", promo_text=None, raw={})
    return ProductStore([mk("A", 12_000_000), mk("B", 11_000_000)])


def test_evaluate_reports_category_accuracy():
    scenarios = [{"message": "tu lanh 20tr tiet kiem dien", "expect_category": "tu_lanh",
                  "expect_budget_max": 20000000, "expect_prefs": ["tiết kiệm điện"]}]
    llm = FakeLLM(json_responses=[{"category": "tu_lanh", "budget_max": 20000000,
                                   "prefs": ["tiết kiệm điện"], "known": ["category", "budget_max", "prefs"]}])
    m = evaluate(scenarios, llm, _store())
    assert m["category_acc"] == 1.0
    assert m["budget_acc"] == 1.0
    assert m["pref_recall"] == 1.0
```

- [ ] **Step 3: Implement eval module** — `backend/app/eval_utils.py`

```python
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
```

- [ ] **Step 4: Implement CLI** — `backend/eval/run_eval.py`

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/pytest tests/test_eval.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Chạy eval thật (thủ công, với LLM + catalog thật)**

```bash
cd backend && ./.venv/Scripts/python eval/run_eval.py
```
Expected: in `category_acc`, `budget_acc`, `pref_recall`, `hallucination_rate`. Mục tiêu MVP: `category_acc ≥ 0.9`, `hallucination_rate = 0.0` (guardrail fail-closed ⇒ bằng 0 theo thiết kế). Nếu `hallucination_rate > 0`, kiểm tra verifier/orchestrator.

- [ ] **Step 7: Commit**

```bash
git add backend/eval/ backend/app/eval_utils.py backend/tests/test_eval.py
git commit -m "feat: scenario eval harness (need accuracy + hallucination rate)"
```

---

### Task 21: Pilot roadmap + README + architecture doc (deliverables D2)

**Files:**
- Create: `docs/PILOT.md`, `docs/ARCHITECTURE.md`, `README.md`

**Interfaces:** Không có code; đây là deliverable văn bản bắt buộc theo đề (Lộ trình pilot 1–2 trang, kiến trúc AI giải thích được).

- [ ] **Step 1: README.md** — mục tiêu, cách chạy (build catalog → uvicorn → vite), biến môi trường, cấu trúc thư mục, cách chạy test & eval. Nêu rõ 6 category hỗ trợ và giới hạn dữ liệu (không có tồn kho/review → luôn "chưa có dữ liệu").

- [ ] **Step 2: docs/ARCHITECTURE.md** — sơ đồ pipeline (preprocess → NLU → clarify → retrieval → provenance → generate → verify), giải thích cơ chế RAG (structured hard-filter + deterministic scoring + optional semantic), và 3 lớp guardrail chống bịa: (a) LLM chỉ nhận facts đã sourced, (b) prompt cấm bịa + buộc nói "chưa có dữ liệu", (c) verifier fail-closed chặn số không nguồn. Map từng mục sang tiêu chí chấm (F: 3×10%).

- [ ] **Step 3: docs/PILOT.md** (1–2 trang) — bám D3: quy mô pilot (1 nhóm ngành, 1.000–10.000 hội thoại), 3 tháng; tích hợp API thật (catalog/price/promotion/stock) thay mock; KPI ký hợp đồng (độ đúng thông tin, 0 hallucination nghiêm trọng, có log nguồn); lộ trình đổi LLM sang model on-prem; kế hoạch bổ sung tồn kho/review/trả góp qua API doanh nghiệp; mở rộng cross-sell & chăm sóc sau mua (stretch của section 2.7 VAIC.md).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/PILOT.md docs/ARCHITECTURE.md
git commit -m "docs: README, architecture, pilot roadmap (D2 deliverables)"
```

---

## Definition of Done (toàn MVP)

- [ ] `cd backend && ./.venv/Scripts/pytest -q` — toàn bộ test xanh.
- [ ] `python scripts/build_catalog.py` sinh `catalog.normalized.json` với ~3.960 sản phẩm, 6 category.
- [ ] `uvicorn app.main:app` + `npm run dev` chạy được; demo end-to-end: hiểu câu không dấu → hỏi ngược → top-3 có trade-off → panel nguồn → "chưa có dữ liệu" cho tồn kho/review → tư vấn nâng/hạ ngân sách.
- [ ] `python eval/run_eval.py`: `hallucination_rate = 0.0`, `category_acc ≥ 0.9`.
- [ ] `docs/PILOT.md`, `docs/ARCHITECTURE.md`, `README.md` hoàn chỉnh; repo push GitHub public.

## Mapping tới tiêu chí chấm (đề Phần D & F)

| Yêu cầu đề | Task chịu trách nhiệm |
|---|---|
| D1.1 Hiểu nhu cầu tiếng Việt (không dấu/viết tắt/đơn vị) | Task 7, 8; eval Task 20 (`category_acc`, `pref_recall`) |
| D1.2 Hỏi ngược đúng câu quan trọng | Task 9, 17 |
| D1.3 So sánh ngôn ngữ dễ hiểu | Task 14 |
| D1.4 Top-3 + trade-off + vì sao loại | Task 11 (why-not), 14 |
| D1.5 Không bịa; gắn nguồn; "chưa có dữ liệu" | Task 13, 14, 15, 17; eval `hallucination_rate` |
| D2 Prototype web + repo + kiến trúc RAG + pilot | Task 12, 18, 19, 21 |
| F Hiểu nhu cầu & hỏi ngược (10%) | Task 8, 9 |
| F So sánh + trade-off (10%) | Task 11, 14 |
| F Tính đúng dữ liệu & chống hallucination (10%) | Task 13, 15, 17 |
