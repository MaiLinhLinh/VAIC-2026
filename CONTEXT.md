# CONTEXT — Ngôn ngữ chung của dự án

Glossary các thuật ngữ nghiệp vụ. Chỉ chứa định nghĩa khái niệm — không chứa chi tiết cài đặt.

## Nguồn dữ liệu

- **Spec sheet** — file `data/Spec_cate_gia.xlsx` do đề bài cấp (đợt 2026-07): 8.746 SKU, 14 sheet ứng với 14 category chính thức của đề bài. Mạnh về thông số kỹ thuật có cấu trúc; ~75% dòng không có giá. Không có cột tên sản phẩm.
- **Bản crawl** — file `data/products_detail.json` do đề bài cấp: 13.754 sản phẩm cào từ website dienmayxanh.com (2026-07-17), trải trên ~120 category (rộng hơn nhiều so với 14 category đề bài). Mạnh về thông tin thương mại; chỉ ~1.500 sản phẩm thuộc phạm vi 14 category.
- **Thông tin thương mại** — nhóm dữ liệu chỉ có ở bản crawl: giá bán tại thời điểm cào, rating, lượt bán, chính sách bảo hành, khuyến mãi (promotion), tên sản phẩm hiển thị, URL, ảnh.
- **Khuyến mãi (promotion)** — text khuyến mãi nguyên văn từ bản crawl, có tính thời điểm. Chỉ hiển thị nguyên văn kèm nguồn cho người dùng xem; không bao giờ đưa vào dữ kiện cho trợ lý phát biểu lại (tránh số liệu khuyến mãi hết hạn/không truy nguồn được).

## Ghép nguồn

- **Khoá ghép (join key)** — cặp định danh dùng để nhận ra cùng một sản phẩm ở hai nguồn: `productidweb` (spec sheet) ↔ `product_id` (crawl) là khoá chính; `sku` (spec sheet) ↔ `productcode` (crawl) là khoá phụ. Không dùng so khớp mờ (fuzzy) theo tên.
- **Dòng match** — dòng spec sheet có bản ghi crawl tương ứng qua khoá ghép (~1.337/8.746, ~15%).
- **Enrichment (chèn thông tin thương mại)** — bổ sung thông tin thương mại từ bản crawl vào các dòng match của spec sheet, chỉ qua khoá ghép chính xác. Mục tiêu là thêm thông tin thương mại, *không phải* bù giá: dòng không match giữ nguyên trạng thái "chưa có dữ liệu". Không suy rộng giá trị từ sản phẩm này sang sản phẩm khác.
- **Ưu tiên nguồn (source precedence)** — khi hai nguồn cùng có một trường: **giá** lấy theo bản crawl (mới hơn, có ngày cào), spec sheet chỉ dùng khi crawl không có; **thông số kỹ thuật** luôn lấy theo spec sheet (nguồn nhà sản xuất có cấu trúc). Mọi giá trị đều mang nhãn nguồn.
- **Biến thể (variant)** — nhiều dòng sku trong spec sheet cùng trỏ về một trang sản phẩm web (cùng `productidweb`). Khi enrichment, mọi trường crawl được broadcast cho tất cả biến thể của trang đó (chấp nhận sai số giá/tên giữa các biến thể).
- **Máy hoàn chỉnh / linh kiện** — phân loại dòng trong ngành hàng máy tính để bàn: sheet nguồn trộn máy bộ với linh kiện rời (case, card đồ hoạ, mainboard, quạt/tản nhiệt). Trợ lý mặc định chỉ đề xuất máy hoàn chỉnh; linh kiện chỉ xuất hiện khi khách hỏi đúng loại hàng.
- **Gắn lại nhãn ngữ nghĩa (relabel)** — khi header cột đặt tên sai bản chất giá trị (vd "Phụ kiện đi kèm" chứa hiệu suất mực in, công suất sạc), thông số được đặt lại tên đúng nghĩa; header gốc giữ trong nguyên văn để đối chiếu. Khác với sửa dữ liệu: giá trị không đổi, chỉ đổi tên gọi.

## Hội thoại & tư vấn

- **Khoảng ngân sách (budget range)** — ngân sách khách nêu luôn được hiểu là một *khoảng* [tối thiểu, tối đa], không phải một con số đơn. Khách chỉ nêu mức tối đa ("dưới 15 triệu") → khoảng [0, tối đa]; chỉ nêu mức tối thiểu ("trên 20 triệu") → khoảng [tối thiểu, trần quy ước 1 tỷ đồng] (trần nằm trên mọi giá sản phẩm hiện có, chỉ để khép khoảng — không phải giới hạn nghiệp vụ); nêu một con số không rõ chiều ("tầm/khoảng 15 triệu", "ngân sách 15 triệu") → khoảng [70% × con số, con số] — hiểu là khách muốn hàng *cỡ đó*, không phải "càng rẻ càng tốt".
- **Mô tả tìm kiếm (`search_description`)** — chuỗi phi chuẩn hoá theo từng SKU, gồm ngành, nhãn hàng và các cột thông số có ý nghĩa/đủ độ phủ. Không chứa ID, giá, khuyến mãi, URL/ảnh, rating/lượt bán hay trường vận hành crawl. Đây là trường chính để chấm mức khớp ngôn ngữ; `full_specs_json` vẫn là nguồn fact chi tiết.
- **Thứ tự làm rõ** — hỏi bối cảnh dùng (cho ai/làm gì) → ngân sách → gộp 2–3 trường thuộc `search_description` trong một câu. Sau khoảng 3 lượt hỏi làm rõ thì mời khách chọn top sản phẩm để so sánh; câu khách không biết vẫn tính là một lượt, còn lượt trợ lý đang giải đáp câu hỏi của khách thì không tính.

## Làm sạch

- **Làm sạch (cleaning)** — chuẩn hoá giá trị từng bản ghi + khử trùng lặp, **không mất mát** (giữ đủ số dòng nguồn), **không suy đoán** (thiếu là thiếu, không nội suy). Việc chọn lọc theo phạm vi category không thuộc bước làm sạch.
- **Chưa có dữ liệu** — trạng thái tường minh của một trường không có giá trị khả dụng. Giá 0/trống, placeholder của nguồn ("Đang cập nhật", "Hãng không công bố", "null", "None") đều quy về trạng thái này; không bao giờ hiển thị như số liệu thật, không bao giờ hiểu là "miễn phí". Khác với **vắng mặt**.
- **Vắng mặt (không có)** — sản phẩm thật sự *không có* tính năng/bộ phận đó (vd tủ mini không có ngăn đá, máy không có Inverter). Là thông tin thật, hiển thị được và lọc được. Chỉ áp dụng cho cột tính-năng-tuỳ-chọn; giá trị "Không" ở cột bắt-buộc-phải-có (vd dung tích tổng) là dữ liệu thiếu, quy về "chưa có dữ liệu".
- **Cửa sổ hợp lý (plausibility window)** — khoảng giá trị vật-lý-khả-dĩ của một thông số số học, khai báo theo từng thông số từng ngành hàng. Giá trị ngoài cửa sổ là rác của nguồn: quy về "chưa có dữ liệu" và đếm vào báo cáo bất thường — không rescale, không suy đoán đơn vị.
- **Đơn vị chuẩn của cột (canonical unit)** — đơn vị xuất hiện nhiều nhất trong cột đó. Giá trị mang đơn vị khác được tự chuyển đổi về đơn vị chuẩn *chỉ khi* cùng đại lượng vật lý với hệ số xác định (mm↔cm↔m, g↔kg, W↔kW...); khác đại lượng hoặc không đủ ngữ cảnh chuyển đổi → "chưa có dữ liệu" + báo cáo. Áp dụng cho mọi sheet.
- **Cột nhị phân** — cột bản chất Có/Không: mọi biến thể câu chữ cùng nghĩa ("Không có bơm trợ lực" ≡ "Không có") quy về đúng hai giá trị chuẩn `Có`/`Không có`; nếu câu gốc chứa chi tiết thêm thì giữ kèm nguyên văn. Áp dụng cho mọi sheet.
- **Khoá placeholder** — `productidweb` dạng toàn-số-9 (9999, 99999...) là giá trị giữ chỗ của nguồn, coi như *không có khoá*: không tạo quan hệ biến thể, không enrichment, đếm vào báo cáo. Áp dụng cho mọi sheet.
- **Báo cáo bất thường** — số liệu thống kê các giá trị bất thường gặp khi làm sạch (đếm và báo cáo, không sửa ngầm).
