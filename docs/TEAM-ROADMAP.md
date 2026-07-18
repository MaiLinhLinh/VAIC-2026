# Chia việc 4 người & Lộ trình phát triển — Trợ lý AI Điện Máy Xanh

> Mục tiêu: 4 thành viên phát triển **song song, ít đụng nhau nhất**, tiếp tục từ MVP hiện tại (67 test xanh, đã chạy end-to-end với LLM thật).

## Nguyên tắc làm việc song song

- **Mỗi người 1 nhánh** `feat/<tên-phần>` (vd `feat/data-retrieval`), mở PR về `master`.
- **`app/schemas.py` là HỢP ĐỒNG chung** giữa các phần (Product, NeedProfile, Recommendation, AdviceResult, ...). Ai muốn đổi schema phải báo cả nhóm — đây là ranh giới duy nhất mọi người chạm vào.
- **Test offline bằng `FakeLLM`** → không cần API key, không tốn token khi phát triển.
- **Full suite phải xanh trước khi merge**: `cd backend && ./.venv/Scripts/pytest -q`.
- File `backend/.env` (chứa key thật) **không commit** — mỗi người tự tạo từ `.env.example`.

## Sơ đồ pipeline & ranh giới (ai nhận/trả gì)

```
tin nhắn khách
   │
   ▼  [Phần B] NLU + Hội thoại
NeedProfile ─────────────────────────► (hỏi ngược: SlotQuestion)
   │
   ▼  [Phần A] Truy xuất + Xếp hạng (RAG)
Recommendation (top-3 + why-not)
   │
   ▼  [Phần C] Tư vấn + Chống bịa
AdviceResult (lời tư vấn + fact-card + guardrail)
   │
   ▼  [Phần D] API + Web UI + Eval
Người dùng cuối
```

`app/orchestrator.py` là "keo dán" nối B→A→C; nó mỏng và ổn định (thuộc Phần C, đổi thì báo nhóm).

---

## PHẦN A — Dữ liệu & Truy xuất (RAG core)

**Mục tiêu:** catalog sạch + lọc/xếp hạng chính xác. Đây là "trái tim không bịa" — mọi con số đến từ đây.

**File sở hữu:**
`app/catalog/` (parsers.py, category_config.py, normalize.py, loader.py) · `app/retrieval/` (filters.py, scoring.py, embed.py, engine.py) · `scripts/build_catalog.py` · `tests/test_parsers|category_config|normalize|loader|filters|scoring|engine.py`

**Interface:** nhận `Product`, `NeedProfile` → trả `Recommendation` (`RetrievalEngine(store).recommend(profile)`), `ProductStore`.

**Việc làm ngay:**
- Sửa **template tên sản phẩm** cho gọn/đủ đơn vị (vd bỏ lặp "Máy rửa chén Bosch Máy rửa chén độc lập"; "Máy sấy Lumias 4" → "4 kg").
- **Alias ràng buộc số** (slot → cột thông số) để "dung tích 200 lít", "27 inch" lọc cứng được (hiện fail-open).
- Tinh chỉnh **semantic re-rank** (embeddings, đang tắt mặc định) + đo tác động.

**Hướng phát triển:**
- **Adapter API thật** thay `loader.py`: catalog/price/promotion/stock của đối tác (thay dữ liệu tĩnh + lấp ~71% thiếu giá).
- Thêm **ngành hàng mới** (điện thoại, laptop, máy lạnh — ví dụ trong đề nhưng chưa có trong data) — chỉ cần thêm `CategoryConfig`.
- Vector DB (FAISS/pgvector), **learning-to-rank** theo hành vi, chuẩn hoá đơn vị nâng cao, ảnh/thumbnail sản phẩm.

---

## PHẦN B — Hiểu nhu cầu & Hội thoại (NLU + Dialogue)

**Mục tiêu:** hiểu đúng câu khách (không dấu/viết tắt/đơn vị đời thường) + hỏi ngược thông minh + suy ra chân dung khách.

**File sở hữu:**
`app/nlu/` (preprocess.py, parser.py) · `app/dialogue/` (clarify.py) · `tests/test_preprocess|parser|clarify.py`

**Interface:** nhận `message` + `LLMClient` → trả `NeedProfile`; nhận `NeedProfile` → trả `SlotQuestion` (câu hỏi ngược).

**Việc làm ngay:**
- **Trích "số người" deterministic** ("nhà 4 người"/"gia đình 4 người" → ràng buộc cứng `[4,4]`) — hiện LLM hay bỏ sót nên gợi ý sai kích cỡ (tủ mini cho gia đình). Làm như fallback ngân sách/ngành hàng đã có.
- **Suy luận nhân khẩu học** (mục 2.7 đề bài): độ tuổi/giới tính/nghề nghiệp từ ngữ cảnh, chỉ khi khách nói rõ.

**Hướng phát triển:**
- Hỏi ngược **theo chân dung** (persona-driven): câu hỏi tiếp theo tuỳ đối tượng.
- **Nhớ nhiều lượt** (multi-turn memory), xử lý mơ hồ/mâu thuẫn, code-switching Việt-Anh trong tên/thông số.
- **Phân loại ý định** (mua mới / so sánh / hỏi chính sách / chăm sóc sau mua) để định tuyến luồng.
- Voice input, gợi ý câu hỏi mẫu.

---

## PHẦN C — Tư vấn, Giải thích & Chống bịa (Advice + Guardrail)

**Mục tiêu:** sinh lời tư vấn bình dân có trade-off, gắn nguồn mọi số, guardrail fail-closed, tư vấn nâng/hạ ngân sách, mua kèm.

**File sở hữu:**
`app/advice/` (provenance.py, generate.py, verify.py, budget.py) · `app/orchestrator.py` (keo dán — đổi thì báo nhóm) · `tests/test_provenance|generate|verify|budget|orchestrator.py`

**Interface:** nhận `Recommendation`, `NeedProfile`, `LLMClient` → trả `AdviceResult`, `TurnResult`.

**Việc làm ngay:**
- **Cross-sell / mua kèm** (2.7): sau khi khách chọn, gợi ý phụ kiện + ưu đãi mua kèm (grounded).
- **Guardrail nâng cấp**: hiện chỉ verify *con số*; thêm kiểm chứng **claim định tính** (vd "êm nhất tầm giá" phải đối chiếu dữ liệu).

**Hướng phát triển:**
- **Chăm sóc sau mua** (2.7): lưu lịch sử, trả lời câu hỏi hậu mãi/bảo hành.
- Bảng so sánh trực quan, giải thích "vì sao loại" nâng cao, tinh chỉnh tone, A/B prompt, đa ngôn ngữ output.
- Trích dẫn nguồn dạng inline citation.

---

## PHẦN D — Nền tảng, Giao diện & Đánh giá (Platform + Frontend + Eval)

**Mục tiêu:** API + web UI mượt + session + đo KPI + triển khai + LLM client.

**File sở hữu:**
`app/main.py`, `app/session.py`, `app/llm/client.py` · `frontend/` (toàn bộ) · `eval/` + `app/eval_utils.py` · `docs/` · CI/CD · `tests/test_api|llm_client|eval.py`

**Interface:** gọi `Orchestrator` (API), tiêu thụ response JSON (frontend).

**Việc làm ngay:**
- **Chạy `eval/run_eval.py` với LLM thật** → lấy số KPI (category_acc, hallucination_rate) cho bài nộp.
- **Deploy live URL** (backend + build frontend) cho demo; hoàn tất polish UI.

**Hướng phát triển:**
- **Streaming** câu trả lời (endpoint hỗ trợ `stream:true`), giảm độ trễ cảm nhận.
- Auth/multi-user, lưu **lịch sử hội thoại**, observability/logging (mask PII), dashboard admin.
- **Đổi LLM sang model on-prem** (Qwen/vLLM) — chỉ cần lớp `LLMClient`.
- CI/CD, Docker, responsive/mobile, tối ưu bundle.

---

## Lộ trình tổng (Roadmap)

### Ngắn hạn — hoàn thiện MVP để demo mạnh (48h–1 tuần)
- (B) số người deterministic · (A) tên sản phẩm + alias ràng buộc số · (C) cross-sell cơ bản · (D) deploy live URL + số KPI thật.
- Kịch bản demo phủ đủ 6 ngành, có câu "chưa có dữ liệu", có nâng/hạ ngân sách.

### Trung hạn — sẵn sàng pilot (theo Phần D3/PILOT.md)
- Tích hợp **API thật** catalog/giá/tồn kho/khuyến mãi/review (lấp dữ liệu thiếu).
- Guardrail định tính + log nguồn kiểm chứng ở quy mô 1.000–10.000 hội thoại.
- Chăm sóc sau mua + lịch sử khách (2.7).

### Dài hạn — sản phẩm thật
- Cá nhân hoá xuyên phiên, đa kênh (web/app/fanpage), voice.
- On-prem LLM, phân tích chuyển đổi, mở rộng toàn bộ ngành hàng.
- A/B testing, tối ưu ranking theo dữ liệu bán thật.

---

## Việc đang chờ (phát hiện khi test) — đã gán chủ

| Việc | Phần | Ưu tiên |
|---|---|---|
| "Số người" chưa thành ràng buộc cứng → gợi ý sai kích cỡ | B | Cao |
| Tên sản phẩm còn lặp/thiếu đơn vị | A | Trung |
| Ràng buộc số (dung tích/inch) fail-open | A | Trung |
| Chạy eval thật lấy KPI cho bài nộp | D | Cao |
| Cross-sell mua kèm (2.7) | C | Trung |

## Chi tiết kiến trúc

Xem `docs/ARCHITECTURE.md` (pipeline + 3 tầng guardrail + map tiêu chí chấm) và `docs/PILOT.md` (lộ trình triển khai).
