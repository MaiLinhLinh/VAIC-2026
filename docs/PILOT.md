# Lộ trình Pilot — Trợ lý AI tư vấn Điện Máy Xanh

*(D3 — bám khung 3 tháng theo đề bài VAIC 2026)*

## 1. Quy mô & mục tiêu pilot

- **Phạm vi:** 1 nhóm ngành hàng duy nhất cho đợt pilot đầu (đề xuất **Tủ lạnh** — nhóm SKU lớn nhất trong Dataset, 1.692 sản phẩm, đã có đủ dữ liệu spec để test độ phủ retrieval), triển khai tại một số cửa hàng/kênh online thí điểm thay vì rollout toàn hệ thống.
- **Khối lượng hội thoại mục tiêu:** 1.000–10.000 hội thoại thật trong suốt pilot, tăng dần theo 3 giai đoạn (xem mục 2), đủ để có tín hiệu thống kê về `category_acc`, `pref_recall`, `hallucination_rate` trên dữ liệu thật thay vì chỉ tập `eval/scenarios.jsonl` nội bộ.
- **Thời lượng:** 3 tháng, chia 3 giai đoạn 1 tháng/giai đoạn.

## 2. Ba giai đoạn triển khai

| Giai đoạn | Thời gian | Nội dung | Ngưỡng thoát (gate) |
|---|---|---|---|
| **Giai đoạn 1 — Shadow / nội bộ** | Tháng 1 | Chạy song song với tư vấn viên thật (không hiển thị cho khách), đối chiếu đề xuất của bot với lựa chọn thật của khách/nhân viên; vá NLU và `category_config` theo lỗi thực tế phát sinh (câu nói địa phương, viết tắt mới chưa có trong `pref_lexicon`/`ask_slots`) | `hallucination_rate = 0.0` trên toàn bộ log shadow; `category_acc ≥ 0.9` |
| **Giai đoạn 2 — Pilot có kiểm soát** | Tháng 2 | Bật cho một nhóm khách hàng thật (vd khách truy cập trang tủ lạnh trên 1 kênh chỉ định), ~1.000–3.000 hội thoại, có nút phản hồi "đề xuất có hữu ích không" | Tỉ lệ hội thoại có ít nhất 1 warning "số chưa truy được nguồn" bị verifier chặn = 0 (guardrail không để lọt); tỉ lệ phản hồi hữu ích ≥ ngưỡng thoả thuận với đối tác |
| **Giai đoạn 3 — Mở rộng quy mô** | Tháng 3 | Mở rộng lên 10.000 hội thoại, tích hợp API thật (mục 3), chuẩn bị KPI ký hợp đồng chính thức (mục 4) | Đạt đủ KPI mục 4 để chuyển sang vận hành thương mại |

## 3. Tích hợp dữ liệu thật (thay thế phần mock/thiếu hiện tại)

MVP hiện dùng `Dataset.xlsx` tĩnh do đề bài cấp, trong đó **~71% dòng không có giá** và **không có cột tồn kho/review**. Với pilot, các nguồn này cần được thay bằng API thật của doanh nghiệp:

- **Catalog & giá:** API sản phẩm/giá thời gian thực thay cho file Excel tĩnh — bảo toàn nguyên tắc "giá luôn gắn nguồn + thời điểm cập nhật" đã có sẵn trong `SourcedValue.provenance` (`app/schemas.py`), chỉ đổi nguồn ghi (`source="catalog"` → `source="pricing-api", as_of=<timestamp>`), không cần đổi kiến trúc.
- **Khuyến mãi:** API promotion để `promo_text` phản ánh khuyến mãi đang chạy thay vì trường tĩnh trong Excel.
- **Tồn kho:** API tồn kho theo cửa hàng/kho — bổ sung field `stock` vào `Product` với cùng cơ chế `SourcedValue` (available/missing), để khi API không trả về vẫn tự động rơi về "chưa có dữ liệu" đúng như cơ chế hiện tại, không cần sửa guardrail.
- **Review/đánh giá:** tích hợp API đánh giá khách hàng (nếu doanh nghiệp có), hiển thị dưới dạng fact có nguồn (`source="review-api"`), tránh để bot tự tổng hợp cảm tính từ text.
- **Trả góp:** API trả góp/tài chính đối tác — cùng cơ chế, disclose rõ điều kiện trả góp lấy từ nguồn nào.

Nhờ kiến trúc `SourcedValue` + fact-card + verifier đã tách rời khỏi nguồn dữ liệu cụ thể, việc thay catalog Excel bằng API thật **không đòi hỏi viết lại pipeline** — chỉ cần viết adapter mới ở lớp `app/catalog/loader.py` trả về cùng schema `Product`.

## 4. KPI ký hợp đồng với đối tác

| KPI | Định nghĩa | Cách đo |
|---|---|---|
| Độ đúng thông tin | % số liệu (giá/spec) hiển thị cho khách khớp với hệ thống nguồn tại thời điểm trả lời | Đối chiếu log fact-card với snapshot API nguồn theo `as_of` |
| 0 hallucination nghiêm trọng | Không có trường hợp bot đưa ra số liệu (giá/spec/khuyến mãi/tồn kho) không truy được nguồn tới khách hàng | Guardrail fail-closed (lớp c, `docs/ARCHITECTURE.md` mục 3) tự chặn ở runtime; **log nguồn đầy đủ mọi câu trả lời** (fact card + nguồn + as_of, không log nội dung PII) để audit định kỳ, không chỉ tin vào tự báo cáo |
| Độ phủ hỏi ngược đúng | % hội thoại bot hỏi đúng ≤3 câu quyết định, không hỏi lại thông tin đã biết | Log `asked` slots + phản hồi khách "sao hỏi hoài" |
| Tỉ lệ đề xuất hữu ích | % khách xác nhận top-3 có sản phẩm phù hợp (nút phản hồi) | Log phản hồi UI |

Log nguồn (source logging) là điều kiện tiên quyết để KPI "0 hallucination nghiêm trọng" có thể kiểm chứng độc lập bởi đối tác, không chỉ dựa vào báo cáo nội bộ.

## 5. Lộ trình đổi LLM sang mô hình on-prem

Kiến trúc đã tách LLM sau interface `LLMClient` (`complete_json`/`complete_text`, `app/llm/client.py`), MVP hiện dùng DeepSeek-V4-Flash qua endpoint tương thích OpenAI. Lộ trình:

1. **Giai đoạn pilot:** giữ nguyên LLM cloud (kiểm soát chi phí vận hành thấp, tốc độ triển khai nhanh).
2. **Đánh giá on-prem:** chạy song song một model on-prem (vd Qwen/Llama fine-tune tiếng Việt, hoặc DeepSeek self-host) qua cùng interface `LLMClient`, so sánh `category_acc`/`pref_recall`/độ trễ trên cùng tập `eval/scenarios.jsonl` mở rộng bằng log pilot thật.
3. **Chuyển đổi:** khi model on-prem đạt tương đương, đổi implementation `LLMClient` (không đổi orchestrator/guardrail) — phù hợp cho giai đoạn doanh nghiệp cần kiểm soát dữ liệu khách hàng nội bộ, giảm phụ thuộc API bên ngoài. Guardrail 3 lớp không đổi vì nó độc lập với việc LLM chạy ở đâu.

## 6. Mở rộng cross-sell & chăm sóc sau mua (ngoài phạm vi MVP hiện tại)

Theo mô tả luồng hoạt động đầy đủ ở mục 2.7 đề bài, MVP hiện dừng ở bước tư vấn/đề xuất top-3; các bước sau nằm ngoài phạm vi MVP và là hướng mở rộng cho pilot/giai đoạn sau:

- **Cross-sell tại điểm mua:** sau khi khách xác nhận chọn sản phẩm, gợi mở phụ kiện/dịch vụ đi kèm (vd mua tủ lạnh gợi ý thêm gói vệ sinh định kỳ), dùng cùng nguyên tắc gắn nguồn — không bịa khuyến mãi phụ kiện.
- **Chăm sóc sau mua:** lưu lịch sử mua hàng (cần thiết kế lưu trữ có PII-consent, khác với session RAM hiện tại vốn cố tình không lưu để bảo mật), cho phép bot trả lời câu hỏi hậu mãi (bảo hành, hướng dẫn sử dụng) dựa trên sản phẩm đã mua.

## 7. Giới hạn hiện tại & việc cần làm tiếp (next steps)

Nêu trung thực để tránh overclaim khi vào pilot:

1. **Ràng buộc số đơn lẻ ngoài ngân sách & số người** (vd khách chốt "đúng 200 lít") hiện **chưa được ép cứng tuyệt đối** — `apply_hard_filters` áp dụng dung sai ±25% cho các ràng buộc số khác qua tên field đoán (`p.number(key.capitalize())`), tức là **fail open** (thà lấy dư ứng viên còn hơn loại nhầm sản phẩm đúng do lệch tên field). Hướng khắc phục đã xác định: thêm bảng alias slot → spec field tường minh trong `category_config` để ràng buộc số áp đúng field, loại bỏ phụ thuộc vào quy ước đặt tên.
2. **Semantic re-rank tắt mặc định** (`ENABLE_EMBEDDINGS=false`) — deterministic scoring là xương sống bắt buộc, semantic chỉ là lớp tinh chỉnh tuỳ chọn cần cài thêm `sentence-transformers` và tải model multilingual; pilot cần đánh giá thêm chi phí độ trễ trước khi bật mặc định.
3. **Nhận diện ý định nâng/hạ ngân sách hiện dựa trên từ khoá** (danh sách `_CHEAPER_KW`/`_PRICIER_KW` trong `orchestrator.py`), chưa qua NLU tổng quát — đủ cho pilot phạm vi hẹp nhưng cần thay bằng phân loại ý định tổng quát hơn khi mở rộng ngành hàng.
4. **Giá/tồn kho/review thật phụ thuộc API đối tác cấp tại thời điểm pilot** — hiện tại placeholder là "chưa có dữ liệu" trung thực, không phải giả lập số liệu; tiến độ tích hợp thực tế phụ thuộc lịch cấp API của đối tác (mục 3), là rủi ro lịch trình cần theo dõi ngay từ đầu Giai đoạn 1.
