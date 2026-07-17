# Kiến trúc hệ thống — Trợ lý AI tư vấn Điện Máy Xanh

Tài liệu này mô tả pipeline xử lý một lượt hội thoại (turn), cơ chế RAG áp dụng trong bài toán tư vấn sản phẩm điện máy, và 3 lớp guardrail chống bịa số liệu — phần được xem là "explainable AI architecture" (D2) của bản nộp.

## 1. Sơ đồ pipeline một lượt hội thoại

```
Khách nhắn (tiếng Việt tự nhiên, có thể không dấu/viết tắt)
        │
        ▼
[1] PREPROCESS (deterministic)            app/nlu/preprocess.py
    - strip_accents: "tu lanh" ~ "tủ lạnh"
    - expand_shorthand: "20tr" -> "20 triệu", "18m2" -> "18 m²"
    - parse_budget_vnd, detect_category (fallback, không phụ thuộc LLM)
        │
        ▼
[2] NLU PARSE (LLM + fallback)            app/nlu/parser.py
    - LLM trích JSON NeedProfile: category, budget_min/max, constraints,
      prefs, demographics, known
    - Prompt buộc: "CHỈ trích xuất những gì khách NÓI RÕ" -> field chưa
      nói giữ nguyên null, không suy đoán
    - Nếu LLM bỏ sót category/budget -> fallback deterministic bù vào
    - merge() với NeedProfile trước đó (không hỏi lại điều đã biết)
        │
        ▼
[3] CLARIFY POLICY (deterministic)         app/dialogue/clarify.py
    - Hỏi tối đa 3 câu, MỖI LẦN MỘT CÂU, câu quan trọng (importance) trước
    - Không bao giờ hỏi lại slot đã có trong asked/constraints
    - Khách từ chối trả lời ("gợi ý đại đi") -> vẫn tiến tới đề xuất,
      kèm ghi rõ assumptions đã giả định
        │  (đủ thông tin, hoặc đã hỏi đủ 3 câu / khách từ chối)
        ▼
[4] RETRIEVAL = RAG cho bài toán này        app/retrieval/engine.py
    a) Hard filter: budget_min/max, ràng buộc số người, ràng buộc số
       (filters.py) — sản phẩm KHÔNG có giá khả dụng bị loại khỏi xếp
       hạng theo ngân sách thay vì bịa giá
    b) Deterministic preference scoring: mỗi pref khách nêu ánh xạ qua
       category_config.pref_lexicon sang 1+ spec field, min-max
       normalize trên toàn bộ ứng viên, cộng dồn có trọng số
       (scoring.py: score_products)
    c) (tuỳ chọn) Semantic re-rank: nếu ENABLE_EMBEDDINGS=true, cộng
       thêm bonus có trọng số 0.3 từ cosine similarity câu truy vấn với
       spec_doc, dùng sentence-transformers multilingual (embed.py).
       Mặc định TẮT — deterministic scoring là xương sống, semantic chỉ
       là lớp tinh chỉnh không bắt buộc.
    d) select_top3: chọn 3 sản phẩm điểm cao nhất, có ràng buộc đa dạng
       (khác brand, trải giá) để tránh top-3 trùng lặp một dòng máy
    e) why_not_group: nếu có exclusion_rules khớp pref khách ưu tiên
       (vd ưu tiên tiết kiệm điện -> loại nhóm không-inverter dù rẻ hơn),
       trả về nhóm bị loại kèm lý do
        │
        ▼
[5] PROVENANCE (deterministic)              app/advice/provenance.py
    - build_fact_card: với mỗi sản phẩm trong top-3, dựng FactCard gồm
      các dòng ĐÃ CÓ GIÁ TRỊ (giá + nguồn "catalog", spec liên quan tới
      pref đã khớp + nguồn "thông số nhà sản xuất") và danh sách missing
      (giá/spec không có + LUÔN CÓ: tồn kho, review, trả góp)
    - facts_for_llm: gộp toàn bộ fact card thành 1 khối text đưa cho LLM
        │
        ▼
[6] GENERATE (LLM, chỉ nhìn thấy facts đã sourced)   app/advice/generate.py
    - System prompt: giọng tư vấn thân thiện, CẤM bịa giá/spec/khuyến
      mãi/tồn kho, buộc nói "chưa có dữ liệu" cho mục nằm trong missing
    - LLM chỉ nhận facts_for_llm + excluded_txt (vì sao loại) + prefs
      khách nêu -> không có quyền truy cập catalog thô
        │
        ▼
[7] VERIFY = guardrail fail-closed           app/advice/verify.py
    - extract_numbers(message) đối chiếu allowed_numbers(cards)
      (mọi số xuất hiện trong fact card, đã canonical hoá dấu . , )
    - Số nào ngoài allowed và ngoài _SAFE (0..99, số đời thường) ->
      warning "Số chưa truy được nguồn"
    - is_grounded() = False nếu có warning này
        │
        ▼
[8] ORCHESTRATOR quyết định câu trả lời cuối    app/orchestrator.py
    - is_grounded == True  -> dùng message của LLM
    - is_grounded == False -> BỎ message LLM, dùng _safe_summary() dựng
      thẳng từ fact card (giá lấy trực tiếp từ card) -> fail CLOSED,
      không bao giờ để lọt số không nguồn ra người dùng
        │
        ▼
Trả về: reply + advice.cards (hiển thị "Vì sao em đề xuất máy này?"
        trong SourcePanel) + assumptions + warnings
```

Nhánh phụ: **tư vấn nâng/hạ ngân sách** (`orchestrator._budget_turn`, `app/advice/budget.py`) được kích hoạt bằng từ khoá ("rẻ hơn"/"cao cấp hơn"...) sau khi đã có một đề xuất (`stage == "recommended"`). Nhánh này build lại profile với ngân sách dịch chuyển quanh giá anchor (đề xuất top-1 gần nhất) — hướng "rẻ hơn" hạ trần ngân sách còn 70% anchor, hướng "cao cấp hơn" mở khoảng 100%–140% anchor — gọi lại RetrievalEngine, và dựng câu trả lời **hoàn toàn deterministic** từ `format_vnd` trên giá catalog — grounded by construction, không qua LLM nên không cần verifier.

## 2. Cơ chế RAG trong bài toán này

RAG ở đây **không phải** vector-search-only: catalog điện máy có cấu trúc (bảng spec rõ field), nên retrieval dùng kết hợp:

1. **Structured hard filter** — lọc cứng theo ngân sách và ràng buộc số (không thương lượng: sai ngân sách thì loại).
2. **Deterministic preference scoring** — mỗi ngành hàng có `pref_lexicon` riêng ánh xạ cụm từ ưu tiên tiếng Việt (vd "tiết kiệm điện", "ít ồn", "pin lâu") sang spec field + hướng tối ưu (min/max), chuẩn hoá min-max trên toàn tập ứng viên rồi cộng có trọng số. Đây là "retrieval" chính — w hoàn toàn diễn giải được (breakdown theo từng pref lưu trong `ScoredProduct.breakdown`).
3. **Semantic re-rank (tuỳ chọn, tắt mặc định)** — cosine similarity giữa câu truy vấn và `spec_doc` bằng `sentence-transformers` (`paraphrase-multilingual-MiniLM-L12-v2`), cộng bonus trọng số thấp (0.3) nếu bật `ENABLE_EMBEDDINGS=true`. Thất bại (thiếu thư viện/model) tự động bỏ qua, không làm sập pipeline — deterministic scoring luôn là đường chính.

Điểm mấu chốt để chống hallucination: **retrieval chỉ chọn SẢN PHẨM (id/hàng trong catalog), không sinh text mô tả.** Mọi text mô tả sản phẩm cho khách đều được LLM viết lại, nhưng LLM chỉ được cấp facts đã trích sẵn từ đúng hàng đó (bước 5), không phải toàn bộ catalog — nên LLM không có "chỗ" để lấy số liệu ở đâu khác ngoài facts hợp lệ.

## 3. Ba lớp guardrail chống bịa số liệu

| Lớp | Cơ chế | File | Chặn được gì |
|---|---|---|---|
| (a) Cấp facts đã sourced | LLM sinh câu tư vấn chỉ nhận `facts_for_llm(cards)` — khối text liệt kê giá/spec đã có nguồn cho đúng 3 sản phẩm trong top-3, không đưa catalog thô hay sản phẩm khác vào context | `app/advice/provenance.py`, `app/advice/generate.py` | LLM paraphrase/nội suy số liệu từ catalog không liên quan hoặc từ kiến thức nền |
| (b) Prompt cấm bịa | System prompt liệt kê rõ: cấm bịa giá/spec/khuyến mãi/tồn kho; bắt buộc nói "chưa có dữ liệu" cho field nằm trong `missing` | `ADVICE_SYSTEM_PROMPT` trong `app/advice/generate.py` | Giảm xu hướng model tự tin đoán khi thiếu field (lớp mềm, không tuyệt đối) |
| (c) Verifier fail-closed | Trích toàn bộ số trong message LLM sinh ra, đối chiếu với tập số xuất hiện trong fact card; số lạ -> đánh dấu ungrounded -> orchestrator **loại bỏ** message LLM, thay bằng tóm tắt deterministic dựng thẳng từ card | `app/advice/verify.py` (`verify_advice`, `is_grounded`), `app/orchestrator.py` (`_safe_summary`) | Lớp (b) là "xin" model đừng bịa — lớp (c) là chặn cứng bất kể model có nghe lời hay không; đây là lớp quyết định vì nó không phụ thuộc hành vi của LLM |

Vì lớp (c) không phụ thuộc việc LLM có tuân thủ prompt hay không, hệ thống đạt được thuộc tính: **số liệu đưa tới người dùng luôn truy được về một dòng trong catalog đã chuẩn hoá, hoặc câu trả lời nói "chưa có dữ liệu".** Đây là điều `eval/run_eval.py` đo bằng `hallucination_rate` (0.0 trên các kịch bản chuẩn) và `tests/test_eval.py` xác nhận bằng phản chứng: đưa vào một message cố tình bịa số → hallucination_rate = 1.0, chứng minh verifier có "răng" chứ không phải chỉ ghi log.

Giới hạn cần nêu trung thực: lớp (c) chỉ bắt được số liệu **định lượng** (giá, thông số dạng số). Nhận định định tính sai (vd LLM diễn giải sai ý nghĩa một spec bằng chữ) không bị chặn bởi verifier này — đây là rủi ro còn lại, giảm nhẹ bởi việc facts đưa vào đã rất hẹp và system prompt yêu cầu bám sát facts.

## 4. Mapping tính năng → tiêu chí chấm (Phần D & F)

| Tiêu chí đề bài | Tính năng chịu trách nhiệm | Vị trí trong code |
|---|---|---|
| D1.1 Hiểu nhu cầu tiếng Việt (không dấu/viết tắt/đơn vị) | `strip_accents`, `expand_shorthand`, `parse_budget_vnd`, NLU parser LLM + fallback deterministic | `app/nlu/preprocess.py`, `app/nlu/parser.py` |
| D1.2 Hỏi ngược đúng câu quan trọng, không hỏi tràn lan | Clarify policy: tối đa 3 câu, ưu tiên theo `importance`, không hỏi lại | `app/dialogue/clarify.py`, `category_config.ask_slots` |
| D1.3 So sánh bằng ngôn ngữ dễ hiểu | LLM viết câu tư vấn từ facts, giọng "nhân viên tư vấn thân thiện", không thuật ngữ marketing | `app/advice/generate.py` (`ADVICE_SYSTEM_PROMPT`) |
| D1.4 Top-3 + trade-off + vì sao loại | `select_top3` (đa dạng brand/giá), `why_not_group` (exclusion_rules) | `app/retrieval/scoring.py` |
| D1.5 Không bịa; gắn nguồn; "chưa có dữ liệu" | 3 lớp guardrail (mục 3), fact card có `missing` | `app/advice/provenance.py`, `app/advice/verify.py`, `app/orchestrator.py` |
| D2 Prototype web + repo + kiến trúc RAG + pilot | FastAPI + React chạy end-to-end, tài liệu này, `docs/PILOT.md` | `app/main.py`, `frontend/`, `docs/` |
| **F — Hiểu nhu cầu & hỏi ngược (10%)** | Preprocess + NLU parser + clarify policy; đo bằng `category_acc`, `pref_recall` | `app/nlu/`, `app/dialogue/clarify.py`, `eval/run_eval.py` |
| **F — So sánh + trade-off (10%)** | Top-3 đa dạng, why-not, tư vấn nâng/hạ ngân sách có quy đổi VND cụ thể | `app/retrieval/scoring.py`, `app/advice/budget.py` |
| **F — Tính đúng dữ liệu & chống hallucination (10%)** | 3 lớp guardrail + fail-closed; đo bằng `hallucination_rate` | `app/advice/verify.py`, `app/orchestrator.py`, `eval/run_eval.py`, `tests/test_eval.py` |

Các chỉ số cụ thể (`category_acc`, `pref_recall`, `hallucination_rate`) không được chốt số ở đây — chúng được đo trực tiếp bằng `backend/eval/run_eval.py` chạy trên `backend/eval/scenarios.jsonl`, tránh nêu con số suy đoán không kiểm chứng được trong tài liệu tĩnh này.
