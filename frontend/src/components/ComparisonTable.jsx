export default function ComparisonTable({ table }) {
  if (!table || !table.products?.length || !table.rows?.length) return null
  return (
    <div className="compare">
      <div className="compare-title">So sánh nhanh {table.products.length} máy</div>
      <div className="compare-scroll">
        <table>
          <thead>
            <tr>
              <th className="crit-h">Tiêu chí</th>
              {table.products.map((p, i) => (
                <th key={i}>{p}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, ri) => (
              <tr key={ri}>
                <td className="crit">
                  {row.label}
                  {row.better ? <span className="hint"> · {row.better}</span> : null}
                </td>
                {row.cells.map((c, ci) => (
                  <td
                    key={ci}
                    className={c.is_best ? 'best' : c.available ? '' : 'na'}
                  >
                    {c.value}
                    {c.is_best ? ' ✓' : ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="compare-note">✓ = tốt nhất theo tiêu chí đó. Mọi số lấy trực tiếp từ catalog.</div>
    </div>
  )
}
