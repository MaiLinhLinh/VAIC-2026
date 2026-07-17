import { useState } from 'react'

export default function SourcePanel({ card }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="source">
      <button className="why-btn" onClick={() => setOpen(!open)}>
        {open ? '▲ Ẩn nguồn' : '▼ Vì sao em đề xuất máy này?'}
      </button>
      {open && (
        <div className="source-body">
          <ul>
            {card.lines.map((l, i) => (
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
