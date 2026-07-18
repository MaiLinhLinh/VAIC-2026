import SourcePanel from './SourcePanel'
import ComparisonTable from './ComparisonTable'

export default function Message({ msg }) {
  const { role, text, recommendation } = msg
  return (
    <div className={`msg ${role}`}>
      <div className="bubble" style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
      {recommendation?.warnings?.length > 0 && (
        <div className="warn">
          {/* warning-triangle icon (Heroicons) */}
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
          </svg>
          Có số liệu chưa truy được nguồn — đã ẩn để tránh sai lệch.
        </div>
      )}
      {recommendation?.comparison && <ComparisonTable table={recommendation.comparison} />}
      {recommendation?.cards?.map((c, i) => (
        <div className="card" key={i}>
          <div className="card-title">{c.title.replace('Vì sao em đề xuất ', '').replace('?', '')}</div>
          <SourcePanel card={c} />
        </div>
      ))}
      {recommendation?.assumptions?.length > 0 && (
        <div className="assume">Giả định: {recommendation.assumptions.join(' ')}</div>
      )}
    </div>
  )
}
