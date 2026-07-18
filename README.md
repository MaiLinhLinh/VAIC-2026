# Trợ lý AI tư vấn sản phẩm — Điện Máy Xanh (VAIC 2026, SME track)

Chatbot tư vấn mua điện máy bằng tiếng Việt tự nhiên, hiểu câu không dấu/viết tắt, hỏi ngược đúng câu quan trọng, đề xuất top-3 sản phẩm kèm trade-off và **chống bịa số liệu bằng kiến trúc RAG + verifier fail-closed**. Backend Python/FastAPI, frontend React/Vite, LLM = DeepSeek-V4-Flash qua endpoint tương thích OpenAI (có thể đổi provider bất kỳ lúc nào nhờ interface `LLMClient` — xem [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).

Đây là bản MVP nộp cho vòng SME của VAIC 2026, được xây trên đúng bộ dữ liệu đề bài cấp (`Dataset.xlsx`), không dùng dữ liệu mẫu tự chế.

## 1. Phạm vi dữ liệu & giới hạn (đọc trước khi demo)

Catalog chuẩn hoá từ `Dataset.xlsx`, phủ toàn bộ 6 category trong đề, tổng cộng **~3.960 SKU**:

| Category (code) | Tên hiển thị | Số SKU |
|---|---|---:|
| `tu_lanh` | Tủ lạnh | 1.692 |
| `dong_ho` | Đồng hồ thông minh | 1.336 |
| `man_hinh` | Màn hình máy tính | 469 |
| `tu_mat` | Tủ mát / tủ đông | 222 |
| `may_rua_chen` | Máy rửa chén | 134 |
| `may_say` | Máy sấy quần áo | 107 |

Cả 6 category được xử lý **bình đẳng** qua cấu hình riêng từng ngành hàng (`backend/app/catalog/category_config.py`) — không có category nào được "hard-code" ưu tiên. Lưu ý: bộ demo bám theo 6 category **thật** trong Dataset.xlsx, không phải các ví dụ máy lạnh/điện thoại minh hoạ trong đề bài.

Giới hạn dữ liệu thật, được xử lý trung thực thay vì che giấu:

- **~71% số dòng không có giá khả dụng** (cả cột "giá gốc" lẫn "giá khuyến mãi" đều trống). Hệ thống đánh dấu các dòng này là "chưa có dữ liệu" và **loại khỏi kết quả xếp hạng theo ngân sách** thay vì bịa giá.
- **Không có cột tồn kho hay đánh giá (review)** trong dataset gốc. Vì vậy trợ lý **luôn** trả lời "chưa có dữ liệu" khi được hỏi về tồn kho / review / trả góp — không bao giờ suy đoán hay bịa số.
- Đơn vị bị gộp vào chuỗi text (vd "313 lít", "1720W - 2050W"), giá trị null không nhất quán, và dataset không có cột "tên sản phẩm" riêng (tên được tổng hợp từ brand + thông số nổi bật). Tất cả được chuẩn hoá tại `backend/app/catalog/` (`parsers.py`, `normalize.py`).

## 2. Cách chạy

### Backend

```bash
cd backend
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt

# Sinh catalog chuẩn hoá từ Dataset.xlsx (bắt buộc trước khi chạy server —
# file này không được commit vào git, xem .gitignore)
./.venv/Scripts/python scripts/build_catalog.py
# -> ghi backend/data/catalog.normalized.json (~3.960 sản phẩm, 6 category)

cp .env.example .env
# rồi sửa .env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL (mặc định DeepSeek-V4-Flash
# qua endpoint tương thích OpenAI /chat/completions)

./.venv/Scripts/uvicorn app.main:app --port 8000
```

Biến môi trường (`backend/.env`, mẫu ở `backend/.env.example`):

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `LLM_BASE_URL` | Endpoint tương thích OpenAI (`/chat/completions`) | — |
| `LLM_API_KEY` | API key của LLM | — |
| `LLM_MODEL` | Tên model | `DeepSeek-V4-Flash` |
| `DATASET_PATH` | Đường dẫn `Dataset.xlsx` | `../Dataset.xlsx` |
| `CATALOG_PATH` | Đường dẫn catalog đã chuẩn hoá | `./data/catalog.normalized.json` |
| `ENABLE_EMBEDDINGS` | Bật re-rank ngữ nghĩa (semantic) tuỳ chọn, cần cài `sentence-transformers` | `false` |
| `PIPELINE` | Luồng phục vụ: `agent_core` (LangGraph + SQLite, mặc định) hoặc `orchestrator` (bản cũ, fallback) | `agent_core` |
| `AGENT_DB_PATH` | DB SQLite của agent_core (dùng khi `PIPELINE=agent_core`) | `backend/app/agent_core/products.db` |

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Mở http://localhost:5173 (backend phải đang chạy ở `:8000`; CORS đã mở sẵn cho origin này).

### Test & eval

```bash
cd backend
./.venv/Scripts/pytest -q
# 64 test xanh: preprocess, NLU parser, clarify policy, hard filters, scoring,
# provenance, generate, guardrail verifier, orchestrator, API, eval harness...

./.venv/Scripts/python eval/run_eval.py
# In ra category_acc, budget_acc, pref_recall, hallucination_rate trên tập
# kịch bản backend/eval/scenarios.jsonl. hallucination_rate đo trên các câu
# trả lời có đề xuất (top3 không rỗng): tỉ lệ message còn số không truy được
# nguồn sau verifier — theo thiết kế hệ thống fail-closed nên phải bằng 0.0.
```

## 3. Cấu trúc thư mục

```
backend/
  app/
    catalog/        # chuẩn hoá Dataset.xlsx -> Product (parsers, category_config, loader)
    nlu/             # preprocess (tiếng Việt không dấu, viết tắt, ngân sách) + parser (LLM -> NeedProfile)
    dialogue/        # chính sách hỏi ngược (clarify): tối đa 3 câu, không hỏi lại
    retrieval/       # hard filter + deterministic scoring + why-not + semantic re-rank (tuỳ chọn)
    advice/          # provenance (fact card có nguồn) + generate (LLM) + verify (guardrail) + budget (nâng/hạ)
    agent_core/      # LUỒNG MẶC ĐỊNH: agent-graph LangGraph (StateGraph + MemorySaver) trên SQLite
                     #   intent -> router -> {clarify | detail | retrieve -> advisor -> compare -> verify}
                     #   DeepSeek qua DeepSeekClient; guardrail fail-closed tái dùng advice/verify.py
    orchestrator.py  # luồng cũ (fallback qua PIPELINE=orchestrator); điều phối 1 lượt hội thoại (turn)
    main.py          # FastAPI app (/api/chat, /api/reset, /api/health); chọn engine theo cờ PIPELINE
    session.py       # session state trong RAM, không log nội dung khách (PII-safe)
  scripts/build_catalog.py   # sinh data/catalog.normalized.json từ Dataset.xlsx
  eval/               # scenarios.jsonl + run_eval.py (category_acc, hallucination_rate...)
  tests/              # 64 unit/integration test
frontend/
  src/
    App.jsx, api.js, components/Message.jsx, components/SourcePanel.jsx
docs/
  ARCHITECTURE.md    # pipeline, cơ chế RAG, 3 lớp guardrail, mapping tới tiêu chí chấm
  PILOT.md            # lộ trình pilot 1-2 trang (D3)
Dataset.xlsx           # dữ liệu gốc do đề bài cấp
```

## 4. Cam kết chống bịa số liệu (tóm tắt — chi tiết ở docs/ARCHITECTURE.md)

Mọi câu trả lời có đề xuất sản phẩm đi qua 3 lớp bảo vệ độc lập trước khi tới người dùng:

1. LLM không bao giờ thấy catalog thô — chỉ thấy khối `facts_for_llm` gồm các giá trị **đã được gắn nguồn** (giá từ catalog, thông số từ "thông số nhà sản xuất").
2. System prompt cấm bịa giá/thông số/khuyến mãi/tồn kho, buộc nói "chưa có dữ liệu" cho mọi mục nằm trong danh sách missing.
3. Verifier xác định lại **mọi con số** trong câu trả lời của LLM; số nào không truy được về fact card sẽ khiến hệ thống **fail-closed**: bỏ câu trả lời của LLM, thay bằng bản tóm tắt dựng trực tiếp từ dữ liệu catalog (`_safe_summary` trong `orchestrator.py`).

Kết quả: `eval/run_eval.py` đo `hallucination_rate` trên tập kịch bản — bằng thiết kế, câu trả lời được gắn nguồn đúng cách sẽ luôn cho 0.0; test `tests/test_eval.py` khẳng định câu trả lời cố tình bịa (ungrounded) bị verifier phát hiện và trả về 1.0.


Thuần frontend (không đụng backend):
- Redesign theme xanh da trời, chip gợi ý, sửa chat hòa nền → styles.css, App.jsx, Message.jsx, SourcePanel.jsx, QuickSuggestions.jsx
- Lời chào GREETING (làm mới + đổi câu chữ) → App.jsx
- Vừa rồi: bỏ disable input, autoFocus, nút spinner "đang trả lời" → App.jsx + vài dòng styles.css
- api.js thêm sendChatStream (vẫn là frontend)

Có sửa backend (chỉ cho tính năng streaming, không phải UI):
- app/main.py — endpoint SSE /api/chat/stream + cắt lát hiển thị
- app/orchestrator.py — thêm callback on_status/on_delta (tham số tùy chọn, hành vi cũ giữ nguyên)
- app/llm/client.py — thêm stream_text (stream:true)
- app/advice/generate.py — tách hàm build prompt (refactor, không đổi logic)
- app/advice/verify.py — thêm line_is_grounded
- app/advice/streaming.py — file mới
- Tests: test_api.py (thêm test), test_streaming.py (mới)
