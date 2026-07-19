// Quick-start suggestion chips shown when the conversation is fresh.
// Clicking a chip sends its message through the normal chat flow.
// Icon paths are Heroicons (outline, 24x24).
const SUGGESTIONS = [
  {
    label: 'Tư vấn sản phẩm',
    message: 'Tôi cần tư vấn chọn sản phẩm phù hợp với nhu cầu.',
    icon: 'M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9',
  },
  {
    label: 'So sánh sản phẩm',
    message: 'Tôi muốn so sánh hai sản phẩm để chọn máy phù hợp hơn.',
    icon: 'M7.5 21 3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5',
  },
  {
    label: 'Mua theo ngân sách',
    message: 'Gợi ý giúp tôi sản phẩm tốt nhất trong ngân sách của tôi.',
    icon: 'M12 6v12m-3-2.818.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  },
  {
    label: 'Bảo hành & đổi trả',
    message: 'Cho tôi hỏi về chính sách bảo hành và đổi trả.',
    icon: 'M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z',
  },
]

export default function QuickSuggestions({ onPick, disabled }) {
  return (
    <div className="suggestions">
      <div className="suggestions-title">Bạn có thể bắt đầu với:</div>
      <div className="suggestions-grid">
        {SUGGESTIONS.map((s) => (
          <button key={s.label} className="chip" disabled={disabled} onClick={() => onPick(s.message)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d={s.icon} />
            </svg>
            <span>{s.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
