# EMX Advisor — Frontend

Giao diện chat tư vấn sản phẩm **Trợ lý AI Điện Máy Xanh**. Single-page app dạng chatbot: người dùng nhắn tin, bot trả lời kèm thẻ đề xuất sản phẩm có trích nguồn dữ liệu.

- **Cập nhật lần cuối:** 18/07/2026
- **Package:** `emx-advisor-frontend` v0.1.0

---

## 1. Tech Stack

| Thành phần | Công nghệ | Phiên bản |
|---|---|---|
| UI framework | React | ^18.3.1 |
| Build tool | Vite + @vitejs/plugin-react | ^5.4.0 / ^4.3.1 |
| Styling | CSS thuần (1 file `styles.css`), CSS variables | — |
| Font | Nunito (Google Fonts, import trong CSS) | 400–800 |
| Icons | SVG inline (Heroicons outline 24×24) | — |
| State | React hooks (`useState`, `useRef`, `useEffect`) — không dùng thư viện state | — |

Không có TypeScript, không có router, không có thư viện UI ngoài — cố ý giữ tối giản (KISS).

## 2. Chạy dự án

```bash
npm install
npm run dev      # dev server: http://localhost:5173
npm run build    # build production → dist/
npm run preview  # xem thử bản build
```

**Yêu cầu backend:** Vite proxy mọi request `/api/*` sang `http://localhost:8000` (cấu hình trong `vite.config.js`). Backend phải chạy trước ở port 8000 thì chat mới hoạt động.

## 3. Cấu trúc thư mục

```
frontend/
├── index.html                          # HTML shell (lang="vi"), mount #root
├── vite.config.js                      # Port 5173 + proxy /api → localhost:8000
├── package.json
└── src/
    ├── main.jsx                        # Entry: render <App /> trong StrictMode
    ├── App.jsx                         # Component gốc: state chat, header, composer
    ├── api.js                          # Gọi API backend (sendChat, resetChat)
    ├── styles.css                      # Toàn bộ style — theme xanh da trời
    └── components/
        ├── Message.jsx                 # 1 tin nhắn (bubble + card đề xuất + cảnh báo)
        ├── SourcePanel.jsx             # Panel "Vì sao đề xuất" (toggle nguồn dữ liệu)
        └── QuickSuggestions.jsx        # Chip gợi ý bấm nhanh khi mới bắt đầu chat
```

## 4. Luồng dữ liệu

```
Người dùng gõ tin / bấm chip gợi ý
        │
        ▼
App.send(text) ──► POST /api/chat/stream (SSE)              (api.js sendChatStream)
        │            │ status  → dòng trạng thái cạnh 3 chấm ("Em đang tìm máy…")
        │            │ delta   → chữ trả lời chạy dần vào bubble bot
        │            └ done    → payload đầy đủ { reply, recommendation }
        ▼
setMessages([...]) ──► render <Message> (bubble + cards + warnings + assumptions)
```

- **Streaming (mặc định):** `send()` gọi `sendChatStream` — nhận sự kiện `status` (hiện trong typing indicator), `delta` (ghép dần vào bubble bot cuối), `done` (thay bubble bằng bản chốt kèm `recommendation`).
- **Fallback:** nếu request stream **không kết nối được** (`err.phase === 'connect'`) → tự động gửi lại 1 lần qua `POST /api/chat` (đồng bộ). Nếu **đứt giữa chừng** (`err.phase === 'stream'`) → KHÔNG gửi lại (backend đã xử lý lượt đó rồi, gửi lại sẽ nhân đôi lượt), chỉ hiện tin lỗi "kết nối bị gián đoạn".
- **Session ID:** tạo 1 lần mỗi tab, dạng `demo-{random}`, lưu `sessionStorage` key `emx_sid`.
- **Nút "Làm mới":** gọi `POST /api/reset { session_id }` rồi reset UI về tin chào ban đầu (lỗi mạng khi reset được bỏ qua, UI vẫn reset).
- **Đang chờ trả lời (`busy`):** ô nhập VẪN gõ được (soạn sẵn câu kế; Enter lúc này không gửi — guard trong `send()` — và không mất chữ); nút gửi disable + icon đổi thành spinner xoay "Đang trả lời…". Typing indicator hiện **dòng trạng thái pipeline** + 3 chấm nhún, tự ẩn khi chữ bắt đầu chạy. Ô nhập có `autoFocus` lúc mở trang.
- **Lỗi API:** hiện tin bot "Xin lỗi, hệ thống đang bận. Anh/chị thử lại nhé."
- **Auto-scroll:** chat tự cuộn xuống cuối mỗi khi `messages` hoặc `status` thay đổi (bám theo chữ đang chạy).

## 5. API Contract (backend cần đáp ứng)

### `POST /api/chat`
```jsonc
// Request
{ "session_id": "demo-abc123", "message": "mua tủ lạnh dưới 20tr" }

// Response
{
  "reply": "string — câu trả lời hiển thị trong bubble",
  "recommendation": {                      // optional
    "cards": [{
      "title": "Vì sao em đề xuất Máy X?", // FE tự cắt prefix "Vì sao em đề xuất " và "?"
      "lines": [{ "label": "Giá", "value": "18.99tr", "source": "dmx.vn" }],
      "missing": ["điện năng tiêu thụ"]    // optional — dữ liệu chưa có
    }],
    "warnings": ["..."],                   // optional — nếu có sẽ hiện chip cảnh báo cam
    "assumptions": ["..."]                 // optional — hiện dòng "Giả định: ..." in nghiêng
  }
}
```

### `POST /api/chat/stream` — SSE (đường mặc định của UI)

Request giống `/api/chat`. Response là `text/event-stream`, mỗi sự kiện 1 dòng `data: {json}`:

```jsonc
{ "type": "status", "text": "Em đang tìm máy phù hợp trong catalog…" } // tiến trình pipeline
{ "type": "delta",  "text": "- Tủ " } // lát 4 ký tự — dòng tư vấn được kiểm chứng xong là "gõ" ra dần
{ "type": "done", "reply": "...", "recommendation": { } } // payload đầy đủ, giống /api/chat
{ "type": "error" }                           // pipeline lỗi → FE hiện tin lỗi
```

**Lưu ý thiết kế (kiểm chứng theo dòng, hiển thị theo ký tự):** backend gọi LLM với `stream:true`, gom token thành **từng dòng hoàn chỉnh**; mỗi dòng được kiểm chứng số liệu với fact card **trước khi** phát (backend: `app/advice/streaming.py`). Dòng đạt kiểm chứng được "gõ" ra thành lát 4 ký tự / 20ms (hằng `LIVE_SLICE_*` trong `main.py`) — trong lúc gõ, worker vẫn đọc tiếp LLM nên khoảng lặng giữa các dòng được lấp, nhìn liền mạch. Nếu một dòng dính số không truy được nguồn → **dừng phát** (fail-closed), sự kiện `done` mang bản tóm tắt an toàn và FE thay toàn bộ bubble bằng nó — đây là đường "rút lại" duy nhất. Các lượt không gọi LLM sinh văn bản (câu hỏi làm rõ, không tìm thấy máy, lỗi stream LLM) thì reply hoàn chỉnh được gửi kiểu typewriter (lát 12 ký tự). FE không cần phân biệt hai chế độ — cùng là chuỗi `delta` rồi `done`.

### `POST /api/reset`
```jsonc
{ "session_id": "demo-abc123" }   // response không dùng đến
```

## 6. Components

### `App.jsx`
- Giữ toàn bộ state: `messages[]` (`{role: 'user'|'bot', text, recommendation?}`), `input`, `busy`, `status` (dòng trạng thái streaming, `null` = không chờ).
- `send(text)`: đường gửi tin duy nhất — cả form composer lẫn chip gợi ý đều đi qua đây. Gửi qua `sendChatStream` (SSE, chữ chạy dần), tự fallback về `sendChat` nếu không kết nối được stream.
- Header: avatar bot (icon chat bubble), tiêu đề, tagline + chấm xanh "online", nút Làm mới.
- Composer: input dạng pill + nút gửi tròn icon máy bay giấy.

### `Message.jsx`
Render 1 tin nhắn. Bot: bubble xanh nhạt trái; User: bubble gradient xanh phải. Nếu có `recommendation`: render warning chip (cam), danh sách `card` sản phẩm, dòng giả định.

### `SourcePanel.jsx`
Nút toggle "Vì sao em đề xuất máy này?" (chevron xoay 180° khi mở) → hiện danh sách `lines` dạng `label: value [nguồn: source]` + phần `missing` nền vàng nhạt.

### `QuickSuggestions.jsx` — Chip gợi ý bắt đầu nhanh

Khi người dùng mới mở app (hoặc vừa bấm "Làm mới"), dưới lời chào của bot hiện **6 chip gợi ý bấm nhanh**:

| Chip | Icon | Tin nhắn gửi khi bấm |
|---|---|---|
| Tư vấn sản phẩm | Hộp hàng | "Tôi cần tư vấn chọn sản phẩm phù hợp với nhu cầu." |
| Khuyến mãi hot | Tag giảm giá | "Hiện đang có chương trình khuyến mãi nào hot không?" |
| Kiểm tra đơn hàng | Check tròn | "Tôi muốn kiểm tra tình trạng đơn hàng của mình." |
| So sánh sản phẩm | Mũi tên hai chiều | "Tôi muốn so sánh hai sản phẩm để chọn máy phù hợp hơn." |
| Mua theo ngân sách | Đồng tiền | "Gợi ý giúp tôi sản phẩm tốt nhất trong ngân sách của tôi." |
| Bảo hành & đổi trả | Khiên bảo vệ | "Cho tôi hỏi về chính sách bảo hành và đổi trả." |

**Cơ chế hoạt động:**
- Điều kiện hiển thị: `messages.length === 1` — tức hội thoại chỉ có đúng 1 tin chào của bot. Vì vậy chip **tự ẩn** ngay khi người dùng gửi tin đầu tiên, và **hiện lại** sau khi bấm "Làm mới".
- Bấm chip → `onPick(message)` → gửi qua **cùng luồng `App.send(text)`** mà form composer dùng (hàm `send` được tách riêng trong `App.jsx` để form và chip dùng chung) → đi qua `sendChat` trong `api.js` như tin nhắn gõ tay, không có đường API riêng.
- Khi `busy` (bot đang trả lời), chip bị disable để tránh gửi trùng.

**Style:** chip dạng pill nền trắng, viền `sky-200`, chữ `sky-700`; hover chuyển nền `sky-50` + viền `sky-500` + đổ bóng nhẹ; icon SVG Heroicons 17px đồng bộ theme; có `:focus-visible` ring cho bàn phím.

**Thêm/bớt/sửa option:** chỉnh mảng `SUGGESTIONS` ở đầu file `QuickSuggestions.jsx`. Mỗi phần tử gồm:
```js
{
  label: 'Tên hiện trên chip',
  message: 'Câu hỏi gửi đi khi bấm',
  icon: 'M...', // path SVG Heroicons outline (viewBox 24×24)
}
```
Không cần sửa CSS hay `App.jsx` khi thêm option mới — grid tự wrap.

## 7. Design System (theme xanh da trời)

Toàn bộ định nghĩa trong `src/styles.css` qua CSS variables (`:root`):

| Biến | Giá trị | Dùng cho |
|---|---|---|
| `--sky-50` | `#f0f9ff` | Nền bubble bot, nền input |
| `--sky-100` | `#e0f2fe` | Viền bubble/khung app, đường kẻ |
| `--sky-200` | `#bae6fd` | Viền chip, viền input |
| `--sky-500` | `#0ea5e9` | Màu chính — gradient, icon, typing dots |
| `--sky-600` | `#0284c7` | Gradient đậm, hover, focus ring |
| `--sky-700` | `#0369a1` | Tiêu đề card, chữ chip |
| `--ink` / `--ink-muted` | `#0f172a` / `#475569` | Chữ chính / chữ phụ (đạt contrast 4.5:1) |
| `--amber` | `#b45309` | Cảnh báo "chưa có dữ liệu" |

**Đặc trưng giao diện:**
- Nền trang: gradient xanh nhạt; khung app 720px nền trắng, viền + bóng xanh để nổi khỏi nền.
- Header: gradient `sky-500 → sky-700`, bo góc dưới 20px.
- Bubble: bo 18px lệch góc (kiểu messenger); user = gradient xanh chữ trắng, bot = `sky-50` viền `sky-100`.
- Font Nunito toàn trang (fallback `system-ui`); cần mạng để tải Google Fonts.
- Animation: tin nhắn trượt vào 0.25s, typing dots nhún, chevron xoay — tất cả tắt khi `prefers-reduced-motion: reduce`.
- Icon: SVG Heroicons inline, không dùng emoji.
- Accessibility: `aria-label` cho input/nút gửi/typing, `:focus-visible` ring rõ ràng, touch target nút gửi 46px.

## 8. Quy ước khi sửa giao diện

1. **Màu sắc:** luôn dùng CSS variables trong `:root`, không hardcode hex rải rác.
2. **Icon:** dùng Heroicons outline (viewBox 24×24, `stroke="currentColor"`), không dùng emoji.
3. **Logic API giữ nguyên trong `api.js`** — component không gọi `fetch` trực tiếp.
4. **Component mới** đặt trong `src/components/`, PascalCase theo convention React hiện có.
5. **File < 200 dòng** — tách component khi phình to.
6. Sau khi sửa, chạy `npm run build` xác nhận không lỗi cú pháp.

---

*Tài liệu này mô tả trạng thái frontend sau đợt redesign theme xanh da trời (07/2026). Khi thay đổi lớn về giao diện/API, cập nhật lại file này.*
