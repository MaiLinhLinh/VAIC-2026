# Thiết kế: Chuyển pipeline phục vụ sang luồng agent_core (LangGraph + DeepSeek-V4-Flash)

- **Ngày:** 2026-07-18
- **Trạng thái:** Chờ duyệt
- **Phạm vi:** `backend/app/agent_core/`, `backend/app/main.py`, `backend/app/config.py`, `requirements.txt`, `.gitignore`, tests. Frontend **không đổi**.

## 1. Mục tiêu

Khi chạy `uvicorn app.main:app`, endpoint `/api/chat` và `/api/chat/stream` phải trả lời **bằng luồng agent_core** (kiến trúc agent-graph), thay vì `Orchestrator` hiện tại — nhưng **giữ nguyên contract API** để frontend chạy y như cũ.

Ba yêu cầu cụ thể của người dùng:

1. **Sửa đường dẫn dữ liệu** trong agent_core để dùng `products.db` mới (đã build sẵn từ `Spec_cate_gia.cleaned.xlsx`), bỏ các path hardcode `d:/Code/Hackathon_V2/`.
2. **Giữ đủ 3 bước** trong luồng: **verify/guardrail** (chống bịa số, fail-closed), **so sánh ứng viên** (bảng compare), **hỏi chi tiết 1 sản phẩm** (deep-dive).
3. **Chuyển LLM sang DeepSeek-V4-Flash** qua `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` trong `.env` (thay Gemini).

Hai quyết định đã chốt với người dùng:

- **Tích hợp:** `main.py` phục vụ qua agent_core, giữ nguyên contract frontend.
- **LangGraph:** cài LangGraph thật, chạy `StateGraph` + `MemorySaver`.

## 2. Hiện trạng (tóm tắt)

| | Pipeline chính (`app/`) | `agent_core/` |
|---|---|---|
| Luồng | Orchestrator hàm-tuần-tự: parse → clarify → retrieve → generate → verify | Agent-graph: intent → router → clarify/retrieve → advisor |
| LLM | **DeepSeek-V4-Flash** (FPT Cloud) qua `DeepSeekClient` | Gemini (LangChain) — **trên giấy, chưa chạy** |
| Dữ liệu | catalog JSON (~3.960 SKU, 6 ngành) | SQLite `products.db` (**8.746 SKU, 14 ngành**) |
| Guardrail | Verifier fail-closed (điểm nhấn bài thi) | Không có (chỉ nối chuỗi suffix) |
| Deps | Đã cài | LangChain/LangGraph **chưa cài** |
| Phục vụ API | ✅ `main.py` | ❌ chưa nối |

**Xác minh dữ liệu:** cả `products.db` ở root lẫn trong `agent_core/` giống hệt nhau (8.746 dòng, 14 ngành); tổng số dòng 14 sheet của `Spec_cate_gia.cleaned.xlsx` = đúng 8.746 → **DB đã đúng, không cần build lại**, chỉ cần sửa đường dẫn code.

## 3. Kiến trúc đích

**Hybrid dựng NGAY TRONG agent_core, native trên dict-row SQLite (14 ngành).** Không kéo các module `advice/*` của pipeline chính sang (chúng bám schema `Product` + `category_config` chỉ cấu hình 6/14 ngành, cần adapter lớn). Thay vào đó tái hiện logic verify/compare/detail trực tiếp trên dict-row của agent_core, **nhưng xuất ra đúng các schema `FactCard` / `ComparisonTable` mà frontend đã tiêu thụ** (import `app.schemas`), để giữ nguyên contract.

**Tái sử dụng tối đa:** `DeepSeekClient` / `get_llm()` (đã xử lý đúng quirk endpoint FPT: `content=None` khi bật json-mode, có `reasoning_content`), `app.schemas`, `app.config.get_settings()`, `app.session`.

### 3.1. Đồ thị LangGraph

```
START → intent_node → [router_edge] ─┬─ "clarify"  → clarify_node  → END
                                     ├─ "detail"   → detail_node   → END
                                     └─ "retrieve" → retrieval_node → advisor_node → compare_node → verify_node → END
```

`StateGraph(AgentState)` compile với `MemorySaver()`. Mỗi lượt gọi `graph.invoke(inputs, config={"configurable": {"thread_id": <sid>, "on_status": ..., "on_delta": ...}})`.

### 3.2. AgentState (TypedDict)

Trường mang qua các lượt (được MemorySaver checkpoint theo `thread_id`):

- `query: str` — câu hỏi lượt hiện tại
- `history: list[dict]` — hội thoại tích luỹ (append mỗi lượt qua reducer)
- `intent: dict` — ý định đã trích (category, budget_max, brand, priority_features, needs_clarification, clarification_questions, is_meta_inquiry)
- `last_products: list[dict]` — ứng viên của lần đề xuất gần nhất (phục vụ compare/detail deep-dive)
- `focused_sku: str | None` — sản phẩm đang được hỏi sâu (sticky focus)
- `retrieval: dict` — kết quả search_products
- `stage: str` — "collecting" | "recommended"
- `question: str | None`
- `response: str` — text trả lời cuối
- `cards: list[dict]`, `comparison: dict | None`, `assumptions: list[str]`, `warnings: list[str]` — để dựng payload
- `next_action: str` — do router quyết định

> Ghi chú reducer: `history` và `last_products` cần được cập nhật đúng qua LangGraph (dùng `Annotated[..., reducer]` khi cần append; các trường còn lại ghi đè theo lượt).

### 3.3. Các node

**intent_node** (thay Gemini → DeepSeek)
- Gọi `DeepSeekClient.complete_json(system, user, schema_hint)` với schema_hint = mô tả `IntentSchema`. Prompt nhồi `get_schema_summary(db_path)` (danh mục từ DB) + các luật ánh xạ danh mục, chống lặp clarification, chuyển chủ đề (giữ nguyên tinh thần prompt cũ).
- Map dict trả về → `IntentSchema` (pydantic validate; thiếu field thì default). Nếu LLM lỗi → `extract_intent_fallback` (heuristic thuần, giữ nguyên).
- Cập nhật `intent`, append `query` vào `history`.

**router_edge**
- Nếu message là "hỏi chi tiết 1 sản phẩm" **và** `last_products` không rỗng **và** không phải chuyển ngành hàng → `"detail"`.
- Nếu `intent.needs_clarification` và chưa đủ slot (`has_enough_slots`) → `"clarify"`.
- Ngược lại → `"retrieve"`.

**clarify_node** — giữ nguyên: trả 1–2 câu hỏi làm rõ, `stage="collecting"`, `question=<câu hỏi>`.

**detail_node** ★ MỚI — hỏi sâu 1 sản phẩm (native trên dict-row)
- `resolve_product_row(message, last_products)`: xác định sản phẩm theo **vị trí** (máy 1/2/3, đầu/giữa/cuối), **hãng** (brand xuất hiện trong câu), hoặc **superlative giá** (rẻ nhất/đắt nhất). Sticky focus: nếu đã có `focused_sku` và không đòi xem lại danh sách → tiếp tục máy đó.
- Dựng **fact-sheet đầy đủ** từ `full_specs_json` + giá + quà (mọi dòng gắn nguồn) → `FactCard(title="Thông tin chi tiết: {name}", ...)`.
- Gọi DeepSeek `complete_text` (system cấm bịa) trả lời thẳng câu hỏi, grounded trong fact-sheet.
- **verify fail-closed:** trích mọi số trong câu trả lời; số nào không có trong fact-sheet → thay bằng `_safe_summary` (giá + hãng cơ bản).
- `stage="recommended"`, card = fact-sheet chi tiết.

**retrieval_node** — `search_products(...)` (giữ nguyên retriever.py, chỉ sửa db_path). Set `last_products` = top ứng viên; giữ nguyên `status` (exact_match / budget_fallback / no_products_found / meta_inquiry).

**advisor_node** — sinh tư vấn top-3 + trade-off bằng DeepSeek
- Dựng `cards` = list `FactCard(title="Vì sao em đề xuất {name}?", lines=[Giá, Thương hiệu, specs chính từ full_specs_json], missing=[tồn kho, review, trả góp])`.
- Dựng `facts_for_llm(cards)` → chỉ đưa **facts đã gắn nguồn** cho LLM (LLM không thấy dữ liệu thô).
- Với `/api/chat/stream`: nếu có `on_delta`, dùng `DeepSeekClient.stream_text` phát **từng dòng đã verify** (line-level grounding, giống `stream_advice`). Với `/api/chat`: gọi `complete_text` blocking.
- Xử lý các status đặc biệt (meta_inquiry, no_products_found, budget_fallback) bằng copy tất định (không qua LLM) như agent_engine hiện có.

**compare_node** ★ MỚI — bảng so sánh side-by-side (dựng thẳng từ DB, KHÔNG qua LLM)
- Nếu ≥ 2 ứng viên: `ComparisonTable(products=[tên], rows=[...])`.
- Hàng **Giá** (rẻ hơn = tốt hơn, đánh dấu `is_best`), hàng **Thương hiệu**, và một số **spec số chung** trích được từ `full_specs_json` (vd điện năng tiêu thụ, dung tích, độ ồn…) với hướng tốt hơn suy theo tên field khi khả dĩ; ô thiếu dữ liệu → `available=false`, giá trị "chưa có dữ liệu".
- Xuất đúng shape `ComparisonTable` (products/rows/cells{value,available,is_best}/better).

**verify_node** ★ MỚI (guardrail thật, fail-closed) — cho luồng đề xuất
- Gom `allowed_numbers` từ tất cả `cards`.
- Trích mọi số trong `response`; số nào không thuộc allowed và không "an toàn" (0–99) → thêm cảnh báo `warnings` và **thay `response` bằng `_safe_summary`** dựng từ cards (fail-closed). Với luồng stream, dòng vi phạm đã bị chặn tại advisor_node; `done` payload cuối cùng thay bằng safe summary khi cần.

### 3.4. Sửa đường dẫn dữ liệu

- **`config.py`**: thêm `agent_db_path` (mặc định resolve tuyệt đối theo vị trí package: `os.path.join(dirname(__file__), "agent_core", "products.db")`), cho phép override bằng `.env` `AGENT_DB_PATH`. Thêm `excel_source_path` cho ingestion (mặc định `../Spec_cate_gia.cleaned.xlsx`).
- **`retriever.py`**, **`agent_engine.py`**: default `db_path` lấy từ config, không phải cwd-relative `"products.db"`.
- **`data_ingestion.py`**: `EXCEL_PATH`/`DB_PATH` → lấy từ config (`Spec_cate_gia.cleaned.xlsx` + `agent_db_path`); chỉ dùng khi cần rebuild.
- **`from retriever import ...`** → import package-relative (`from app.agent_core.retriever import ...`) để import được từ `app.main`.
- Chuẩn hoá **một vị trí DB duy nhất** = `backend/app/agent_core/products.db`. Xoá bản trùng ở root; thêm `products.db` vào `.gitignore` (35MB, regenerate bằng data_ingestion, giống catalog.normalized.json).

### 3.5. LLM — DeepSeek-V4-Flash

- Bỏ hoàn toàn `langchain_google_genai` / `ChatGoogleGenerativeAI` / `ChatPromptTemplate`.
- **Tái dùng `app.llm.client.DeepSeekClient`** (đã có, đã test): `complete_json` (intent, có `_extract_json` robust), `complete_text` (advisor/detail), `stream_text` (streaming).
- Không dùng LangChain `.with_structured_output` / json-mode (endpoint FPT trả `content=None` khi bật json-mode).

### 3.6. Wiring `main.py` (giữ contract)

- Thêm engine mới `AgentCoreEngine.handle_turn(session_id, message, on_status, on_delta) -> payload_dict` bọc `graph.invoke(...)` và map `AgentState` → payload `_turn_payload` hiện tại: `{reply, stage, question, need, recommendation{cards, comparison, assumptions, warnings}}`.
  - `reply` = `response`; `recommendation` = null nếu clarify/collecting, ngược lại gồm cards/comparison/assumptions/warnings.
  - `need` = map `intent` → shape `NeedProfile` (category, budget_max, prefs=priority_features…) để giữ field (frontend không đọc `need`/`stage`/`question` để render nhưng vẫn trả cho tương thích).
- **Cờ chọn pipeline** (an toàn/đảo ngược): `.env` `PIPELINE=agent_core|orchestrator` (mặc định `agent_core`). `get_engine()` trả engine tương ứng. Giữ `Orchestrator` cũ nguyên vẹn để có thể lật lại khi demo lỗi và để test cũ vẫn xanh.
- **Memory & reset:** `thread_id` cho MemorySaver = `f"{session_id}:{epoch}"`, `epoch` giữ trong RAM dict; `/api/reset` tăng `epoch` → checkpoint cũ bị bỏ, hội thoại reset. `/api/health` giữ nguyên.
- **Streaming:** `graph.stream(...)` phát `status` khi qua từng node; advisor_node phát `delta` (dòng đã verify) qua `on_delta` lấy từ `config`. `done` = payload cuối. Giữ nguyên các event `status/delta/done/error`.

## 4. Dependencies

Thêm vào `requirements.txt`: `langgraph` (kéo theo `langchain-core` — chấp nhận được; **không** thêm `langchain-google-genai`). Ghim minor version theo phong cách repo. Cài vào `.venv`.

## 5. Kiểm thử

- **Giữ 64 test cũ xanh** bằng cách không xoá `Orchestrator`/modules và để `test_api.py` chạy nhánh orchestrator (override dependency) hoặc set `PIPELINE=orchestrator` trong fixture.
- **Test mới cho agent_core** (dùng `FakeLLM` hoặc monkeypatch DeepSeekClient) trên DB tạm nhỏ:
  - intent_node map đúng IntentSchema; fallback khi LLM lỗi.
  - router: detail vs clarify vs retrieve.
  - detail_node: resolve theo vị trí/hãng/giá; fail-closed khi bịa số.
  - compare_node: ≥2 ứng viên, đánh dấu is_best đúng, ô thiếu = available:false.
  - verify_node: số không nguồn → fail-closed + warning.
  - API: `/api/chat` với `PIPELINE=agent_core` trả đúng shape payload; `/api/reset` reset memory.
- **Smoke test DeepSeek thật** (thủ công, không trong CI): 1 lượt end-to-end gọi endpoint FPT xác nhận key/model chạy.

## 6. Ngoài phạm vi (YAGNI)

- Không build lại DB (đã đúng dữ liệu).
- Không thêm `langchain-google-genai`, không dùng LangChain cho LLM call.
- Không sửa `category_config.py` (6 ngành) của pipeline chính.
- Không đổi frontend.
- Không thêm luồng nâng/hạ ngân sách tương tác (budget up/down) của orchestrator — agent_core đã có `budget_fallback` trong retriever; các tính năng cross-sell/hậu mãi trong VAIC.md để lần sau.
- Không xoá `Orchestrator` (giữ làm fallback qua cờ `PIPELINE`).

## 7. Rủi ro & giảm thiểu

- **Reducer LangGraph cho `history`/`last_products`:** dễ sai (append vs ghi đè). → Viết test riêng cho việc tích luỹ state qua 2–3 lượt.
- **Streaming trong node LangGraph:** token của DeepSeekClient không được LangGraph tự bắt. → Phát `delta` chủ động qua callback trong `config`, verify theo dòng như `stream_advice` hiện có.
- **MemorySaver reset:** không có API xoá thread gọn. → Đánh version `thread_id` bằng `epoch`.
- **Endpoint FPT quirks:** đã được `DeepSeekClient` xử lý; tái dùng thay vì viết mới.
- **14 ngành, spec không đồng nhất giữa các ngành:** compare/detail đọc `full_specs_json` động, không giả định field cố định; ô thiếu → "chưa có dữ liệu".

## 8. Danh sách file thay đổi (dự kiến)

**Sửa:** `agent_core/agent_engine.py`, `agent_core/retriever.py`, `agent_core/data_ingestion.py`, `app/config.py`, `app/main.py`, `requirements.txt`, `.gitignore`.
**Thêm:** `agent_core/__init__.py` (biến thành package), module map payload trong `main.py` hoặc `agent_core/engine.py` (bọc graph), test mới trong `backend/tests/`.
**Giữ nguyên:** `orchestrator.py` + `advice/*` + `nlu/*` + `retrieval/*` + toàn bộ frontend.
