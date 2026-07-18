import { useState } from 'react'

export default function SourcePanel({ card }) {
  const [open, setOpen] = useState(false)
  const isDetail = card.title?.startsWith('Thông tin chi tiết')
  const openLabel = isDetail ? 'Xem thông số chi tiết' : 'Vì sao em đề xuất máy này?'
  return (
    <div className="source">
      <button className="why-btn" onClick={() => setOpen(!open)}>
        {/* chevron-down icon (Heroicons), rotates when open */}
        <svg className={`chev${open ? ' open' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
        {open ? 'Ẩn nguồn' : openLabel}
      </button>
      {open && (
        <div className="source-body">
          <ul>
            {(card.lines || []).map((l, i) => (
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
