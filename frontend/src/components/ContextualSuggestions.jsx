// Follow-up suggestion chips shown under a bot reply that listed product cards.
// Derived purely from recommendation.cards (no backend involvement) — each chip
// sends its message through the normal chat flow, same as QuickSuggestions.
function shortName(title) {
  return title
    .replace('Vì sao em đề xuất ', '')
    .replace('Thông tin chi tiết: ', '')
    .replace('?', '')
    .trim()
}

export default function ContextualSuggestions({ cards, onPick, disabled }) {
  if (!cards || cards.length === 0) return null
  const names = cards.slice(0, 3).map((c) => shortName(c.title)).filter(Boolean)
  if (names.length === 0) return null

  const chips = []
  if (names.length >= 2) {
    chips.push({
      label: `So sánh ${names[0]} và ${names[1]}`,
      message: `So sánh ${names[0]} và ${names[1]} giúp tôi`,
    })
  }
  chips.push({ label: `Xem chi tiết ${names[0]}`, message: `Cho tôi xem chi tiết ${names[0]}` })
  chips.push({ label: `${names[0]} có trả góp không?`, message: `${names[0]} có hỗ trợ trả góp không?` })
  chips.push({ label: `Đặt mua ${names[0]}`, message: `Tôi muốn đặt mua ${names[0]}` })

  return (
    <div className="contextual-suggestions">
      {chips.map((c) => (
        <button key={c.label} className="chip chip-sm" disabled={disabled} onClick={() => onPick(c.message)}>
          <span>{c.label}</span>
        </button>
      ))}
    </div>
  )
}
