import SourcePanel from './SourcePanel'
import ComparisonTable from './ComparisonTable'

export default function Message({ msg }) {
  const { role, text, recommendation } = msg
  return (
    <div className={`msg ${role}`}>
      <div className="bubble" style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
      {recommendation?.warnings?.length > 0 && (
        <div className="warn">⚠ Có số liệu chưa truy được nguồn — đã ẩn để tránh sai lệch.</div>
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
