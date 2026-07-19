const STATUS_ICON = {
  good: (
    <svg className="status-svg" viewBox="0 0 20 20" fill="currentColor" width="16" height="16">
      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clipRule="evenodd" />
    </svg>
  ),
  warn: (
    <svg className="status-svg" viewBox="0 0 20 20" fill="currentColor" width="16" height="16">
      <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
    </svg>
  ),
  bad: (
    <svg className="status-svg" viewBox="0 0 20 20" fill="currentColor" width="16" height="16">
      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
    </svg>
  )
}

function findCardForProduct(p, cards) {
  if (!cards || !cards.length) return null
  const clean = (s) => {
    if (!s) return ''
    return s
      .toLowerCase()
      .replace(/ví sao em đề xuất /g, '')
      .replace(/thông tin chi tiết: /g, '')
      .replace(/\?/g, '')
      .trim()
  }
  const target = clean(p)
  
  // Exact match
  let match = cards.find(c => clean(c.title) === target)
  if (match) return match
  
  // Substring match
  match = cards.find(c => clean(c.title).includes(target) || target.includes(clean(c.title)))
  if (match) return match

  // Token-based fallback matching
  const targetTokens = target.split(/\s+/).filter(t => t.length > 1)
  if (targetTokens.length > 0) {
    match = cards.find(c => {
      const cTitleClean = clean(c.title)
      const matches = targetTokens.filter(tok => cTitleClean.includes(tok))
      return (matches.length / targetTokens.length) >= 0.7
    })
  }
  return match || null
}

function CellBody({ cell }) {
  if (cell.status && cell.verdict) {
    return (
      <div className={`need-verdict-container`}>
        <div className="need-verdict">
          {STATUS_ICON[cell.status]}
          <span>{cell.verdict}</span>
        </div>
        {cell.detail ? <div className="need-detail">{cell.detail}</div> : null}
      </div>
    )
  }
  return (
    <div className={`spec-cell-container ${cell.is_best ? 'best-spec' : ''}`}>
      <span className="spec-val">{cell.value != null ? cell.value : '—'}</span>
      {cell.is_best ? (
        <span className="spec-best-tag">
          <svg viewBox="0 0 24 24" fill="currentColor" width="10" height="10">
            <path fillRule="evenodd" d="M10.788 3.21c.448-1.077 1.976-1.077 2.424 0l2.082 5.006 5.404.434c1.164.093 1.636 1.545.749 2.305l-4.117 3.527 1.257 5.273c.271 1.136-.964 2.033-1.96 1.425L12 18.354 7.373 21.18c-.996.608-2.231-.29-1.96-1.425l1.257-5.273-4.117-3.527c-.887-.76-.415-2.212.749-2.305l5.404-.434 2.082-5.005Z" clipRule="evenodd" />
          </svg>
          Tốt nhất
        </span>
      ) : null}
    </div>
  )
}

export default function ComparisonTable({ table, cards }) {
  if (!table || !table.products?.length || !table.rows?.length) return null
  const hasTradeoff = table.tradeoff?.length === table.products.length
  return (
    <div className="compare">
      <div className="compare-header">
        <span className="compare-badge">Bảng Đối Chiếu Nhu Cầu</span>
        <h2 className="compare-title">So sánh theo nhu cầu của bạn — {table.products.length} máy</h2>
      </div>
      <div className="compare-scroll">
        <table>
          <thead>
            <tr>
              <th className="crit-h sticky-col">Tiêu chí</th>
              {table.products.map((p, i) => {
                const card = findCardForProduct(p, cards)
                return (
                  <th key={i} className="product-header-cell">
                    {card ? (
                      <div className="comp-product-card">
                        {i === 0 && (
                          <span className="comp-badge-recommended">Gợi ý hàng đầu</span>
                        )}
                        <div className="comp-img-wrapper">
                          {card.image_url ? (
                            <img src={card.image_url} alt={p} />
                          ) : (
                            <div className="comp-img-placeholder">
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                <path strokeLinecap="round" strokeLinejoin="round" d="m21 7.5-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25" />
                              </svg>
                            </div>
                          )}
                        </div>
                        <div className="comp-card-body">
                          <div className="comp-card-title" title={p}>{p}</div>
                          {card.rating && (
                            <div className="comp-card-rating">
                              <svg viewBox="0 0 20 20" fill="currentColor" className="star-icon">
                                <path d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401Z" />
                              </svg>
                              <span>{card.rating}</span>
                              {card.review_count ? <span className="reviews-count">({card.review_count})</span> : null}
                            </div>
                          )}
                          <div className="comp-card-price-row">
                            {card.lines?.find(l => l.label === 'Giá') ? (
                              <>
                                <span className="comp-price">{card.lines.find(l => l.label === 'Giá').value}</span>
                                {card.lines?.find(l => l.label === 'Giá gốc') && (
                                  <span className="comp-price-original">{card.lines.find(l => l.label === 'Giá gốc').value}</span>
                                )}
                              </>
                            ) : (
                              <span className="comp-price-placeholder">Liên hệ shop</span>
                            )}
                          </div>
                          {card.installment && (
                            <div className="comp-card-badges">
                              <span className="comp-badge-inst">Góp 0%</span>
                            </div>
                          )}
                          {card.product_link && (
                            <a href={card.product_link} target="_blank" rel="noopener noreferrer" className="comp-card-btn">
                              <span>Mua ngay</span>
                              <svg viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M5.22 14.78a.75.75 0 001.06 0l7.22-7.22v5.69a.75.75 0 001.5 0v-7.5a.75.75 0 00-.75-.75h-7.5a.75.75 0 000 1.5h5.69l-7.22 7.22a.75.75 0 000 1.06z" clipRule="evenodd" />
                              </svg>
                            </a>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="comp-product-card simple">
                        <div className="comp-card-title simple" title={p}>{p}</div>
                      </div>
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, ri) => (
              <tr key={ri} className={row.is_need_row ? 'need-row' : ''}>
                <td className="crit sticky-col">
                  <div className="crit-wrapper">
                    <span className="crit-label">{row.label}</span>
                    {row.better ? <span className="hint-badge">{row.better}</span> : null}
                  </div>
                </td>
                {row.cells.map((c, ci) => (
                  <td
                    key={ci}
                    className={c.status ? `status-${c.status}` : c.is_best ? 'best' : c.available ? '' : 'na'}
                  >
                    <CellBody cell={c} />
                  </td>
                ))}
              </tr>
            ))}
            {hasTradeoff ? (
              <tr className="tradeoff-row">
                <td className="crit sticky-col">
                  <div className="tradeoff-label-container">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="warning-icon">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
                    </svg>
                    <span>Cân nhắc đánh đổi</span>
                  </div>
                </td>
                {table.tradeoff.map((t, i) => (
                  <td key={i} className="tradeoff-cell">{t}</td>
                ))}
              </tr>
            ) : null}
            <tr className="bottom-cta-row">
              <td className="crit sticky-col">
                <span className="cta-heading-label">Lựa chọn của bạn</span>
              </td>
              {table.products.map((p, i) => {
                const card = findCardForProduct(p, cards)
                return (
                  <td key={i}>
                    {card && card.product_link ? (
                      <a href={card.product_link} target="_blank" rel="noopener noreferrer" className="comp-bottom-cta">
                        Đặt mua ngay
                      </a>
                    ) : (
                      <span className="cta-na">—</span>
                    )}
                  </td>
                )
              })}
            </tr>
          </tbody>
        </table>
      </div>
      <div className="compare-footer">
        <div className="legend">
          <span className="legend-item"><span className="legend-dot status-good">●</span> Vượt trội</span>
          <span className="legend-item"><span className="legend-dot status-warn">●</span> Tạm ổn</span>
          <span className="legend-item"><span className="legend-dot status-bad">●</span> Kém nhất</span>
        </div>
        <div className="note">Mọi thông tin lấy chính xác từ catalog sản phẩm của hãng.</div>
      </div>
    </div>
  )
}
