import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ComparisonTable from './ComparisonTable'
import ContextualSuggestions from './ContextualSuggestions'

function TraceValue({ value }) {
  if (value == null || value === '') return <span className="trace-empty">null</span>
  if (typeof value === 'boolean') return <span>{value ? 'true' : 'false'}</span>
  if (typeof value === 'object') return <pre>{JSON.stringify(value, null, 2)}</pre>
  return <span>{String(value)}</span>
}

function RetrievalTrace({ trace }) {
  if (!trace?.length) return null
  return (
    <details className="trace-panel">
      <summary>
        <span>Truy vết pipeline</span>
        <span className="trace-count">{trace.length} bước</span>
      </summary>
      <div className="trace-body">
        {trace.map((item, index) => (
          <section className="trace-step" key={`${item.step}-${index}`}>
            <div className="trace-step-title">{index + 1}. {item.title}</div>
            <dl>
              {Object.entries(item.data || {}).map(([key, value]) => (
                <div className="trace-row" key={key}>
                  <dt>{key}</dt>
                  <dd><TraceValue value={value} /></dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </details>
  )
}

function CardMeta({ card }) {
  const hasRating = card.rating != null
  if (!hasRating && !card.stock_status && !card.installment) return null
  return (
    <div className="product-card-meta">
      {hasRating && (
        <span className="meta-rating" title={`${card.rating}/5`}>
          <svg viewBox="0 0 20 20" fill="currentColor" className="star-icon">
            <path fillRule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401Z" clipRule="evenodd" />
          </svg>
          {card.rating}{card.review_count ? ` (${card.review_count.toLocaleString('vi-VN')})` : ''}
        </span>
      )}
      {card.stock_status && (
        <span className={`meta-stock ${card.stock_status === 'Còn hàng' ? 'in-stock' : 'out-stock'}`}>
          {card.stock_status}
        </span>
      )}
      {card.installment && (
        <span className="meta-installment" title={card.installment}>
          {card.installment.includes('0%') ? 'Trả góp 0%' : 'Trả góp'}
        </span>
      )}
    </div>
  )
}

export default function Message({ msg, isLast, onSuggest, disabled }) {
  const { role, text, recommendation, trace } = msg
  const [activeCard, setActiveCard] = useState(null)
  const [activeTab, setActiveTab] = useState('specs') // 'specs' or 'promos'

  return (
    <div className={`msg ${role}`}>
      <div className="bubble">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
      {role === 'bot' && <RetrievalTrace trace={trace} />}
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
      {recommendation?.comparison && <ComparisonTable table={recommendation.comparison} cards={recommendation.cards} />}
      
      {recommendation?.cards && recommendation.cards.length > 0 && (
        <div className="product-cards-container">
          {recommendation.cards.map((c, i) => {
            const titleText = c.title
              .replace('Vì sao em đề xuất ', '')
              .replace('Thông tin chi tiết: ', '')
              .replace('?', '');

            // Extract price, original price, and promo
            const priceLine = c.lines?.find(l => l.label === 'Giá');
            const origPriceLine = c.lines?.find(l => l.label === 'Giá gốc');
            const promoLine = c.lines?.find(l => l.label === 'Khuyến mãi/quà kèm');

            // Filter lines to display only real specs in SourcePanel
            const cardBadgeLabels = ['Giá', 'Giá gốc', 'Khuyến mãi/quà kèm', 'Tình trạng', 'Đánh giá', 'Trả góp'];
            const specLines = c.lines?.filter(l => !cardBadgeLabels.includes(l.label)) || [];

            // Parse promotions
            let promos = [];
            if (promoLine && promoLine.value) {
              promos = promoLine.value.split('|').map(p => p.trim()).filter(Boolean);
            }

            return (
              <div className="product-card" key={i}>
                {c.product_link ? (
                  <a href={c.product_link} target="_blank" rel="noopener noreferrer" className="product-card-main">
                    <div className="product-card-img-wrap">
                      {c.image_url ? (
                        <img src={c.image_url} alt={titleText} />
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path strokeLinecap="round" strokeLinejoin="round" d="m21 7.5-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25" />
                        </svg>
                      )}
                      
                      {/* Floating Badges Overlay (Khuyến mãi) */}
                      {promos.length > 0 && (
                        <div className="product-card-badges-overlay">
                          <span className="product-badge promo-badge">
                            <span className="badge-icon">🎁</span>
                            <span className="badge-text" title={promos[0]}>{promos[0]}</span>
                            {promos.length > 1 && (
                              <span className="promo-count-badge" title={promos.slice(1).join(', ')}>
                                +{promos.length - 1}
                              </span>
                            )}
                          </span>
                        </div>
                      )}
                    </div>

                    <div className="product-card-body">
                      <div className="product-card-title" title={titleText}>{titleText}</div>

                      {/* Price Row */}
                      {(priceLine || origPriceLine) && (
                        <div className="product-card-price-row">
                          {priceLine && <span className="product-card-price">{priceLine.value}</span>}
                          {origPriceLine && <span className="product-card-price-original">{origPriceLine.value}</span>}
                        </div>
                      )}

                      <CardMeta card={c} />

                      <div className="product-card-btn">
                        <span>Đặt hàng ngay</span>
                        <svg viewBox="0 0 20 20" fill="currentColor">
                          <path fillRule="evenodd" d="M5.22 14.78a.75.75 0 001.06 0l7.22-7.22v5.69a.75.75 0 001.5 0v-7.5a.75.75 0 00-.75-.75h-7.5a.75.75 0 000 1.5h5.69l-7.22 7.22a.75.75 0 000 1.06z" clipRule="evenodd" />
                        </svg>
                      </div>
                    </div>
                  </a>
                ) : (
                  <div className="product-card-main static">
                    <div className="product-card-img-wrap">
                      {c.image_url ? (
                        <img src={c.image_url} alt={titleText} />
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path strokeLinecap="round" strokeLinejoin="round" d="m21 7.5-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25" />
                        </svg>
                      )}
                      
                      {/* Floating Badges Overlay (Khuyến mãi) */}
                      {promos.length > 0 && (
                        <div className="product-card-badges-overlay">
                          <span className="product-badge promo-badge">
                            <span className="badge-icon">🎁</span>
                            <span className="badge-text" title={promos[0]}>{promos[0]}</span>
                            {promos.length > 1 && (
                              <span className="promo-count-badge" title={promos.slice(1).join(', ')}>
                                +{promos.length - 1}
                              </span>
                            )}
                          </span>
                        </div>
                      )}
                    </div>

                    <div className="product-card-body">
                      <div className="product-card-title" title={titleText}>{titleText}</div>

                      {/* Price Row */}
                      {(priceLine || origPriceLine) && (
                        <div className="product-card-price-row">
                          {priceLine && <span className="product-card-price">{priceLine.value}</span>}
                          {origPriceLine && <span className="product-card-price-original">{origPriceLine.value}</span>}
                        </div>
                      )}

                      <CardMeta card={c} />
                    </div>
                  </div>
                )}
                
                <button 
                  className="product-card-details-btn"
                  onClick={() => {
                    setActiveCard({
                      titleText, specLines, promos, missing: c.missing, product_link: c.product_link,
                      reviews: c.reviews || [], rating: c.rating, review_count: c.review_count,
                    });
                    setActiveTab(specLines.length > 0 ? 'specs' : 'promos');
                  }}
                >
                  Xem chi tiết & Khuyến mãi
                </button>
              </div>
            );
          })}
        </div>
      )}

      {isLast && role === 'bot' && onSuggest && (
        <ContextualSuggestions cards={recommendation?.cards} onPick={onSuggest} disabled={disabled} />
      )}

      {recommendation?.assumptions?.length > 0 && (
        <div className="assume">Giả định: {recommendation.assumptions.join(' ')}</div>
      )}

      {/* Detail Modal Overlay */}
      {activeCard && (
        <div className="modal-overlay" onClick={() => setActiveCard(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">{activeCard.titleText}</h3>
              <button className="modal-close-btn" onClick={() => setActiveCard(null)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            
            <div className="modal-tabs">
              <button 
                className={`modal-tab-btn ${activeTab === 'specs' ? 'active' : ''}`}
                onClick={() => setActiveTab('specs')}
              >
                Thông số kỹ thuật
              </button>
              <button
                className={`modal-tab-btn ${activeTab === 'promos' ? 'active' : ''}`}
                onClick={() => setActiveTab('promos')}
              >
                Khuyến mãi ({activeCard.promos.length})
              </button>
              {activeCard.reviews.length > 0 && (
                <button
                  className={`modal-tab-btn ${activeTab === 'reviews' ? 'active' : ''}`}
                  onClick={() => setActiveTab('reviews')}
                >
                  Đánh giá ({activeCard.reviews.length})
                </button>
              )}
            </div>
            
            <div className="modal-body">
              {activeTab === 'specs' ? (
                <div className="modal-specs-list">
                  {activeCard.specLines.map((l, i) => (
                    <div className="modal-spec-item" key={i}>
                      <span className="spec-label">{l.label}</span>
                      <span className="spec-value">{l.value} <span className="spec-src">({l.source})</span></span>
                    </div>
                  ))}
                  {activeCard.specLines.length === 0 && <p className="empty-text">Chưa có thông số kỹ thuật.</p>}
                  {activeCard.missing && activeCard.missing.length > 0 && (
                    <div className="modal-missing-specs">
                      Chưa có dữ liệu: {activeCard.missing.join(', ')}
                    </div>
                  )}
                </div>
              ) : activeTab === 'reviews' ? (
                <div className="modal-reviews-list">
                  {activeCard.rating != null && (
                    <div className="modal-rating-summary">
                      <svg viewBox="0 0 20 20" fill="currentColor" className="star-icon">
                        <path fillRule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401Z" clipRule="evenodd" />
                      </svg>
                      <span className="rating-big">{activeCard.rating}/5</span>
                      {activeCard.review_count != null && (
                        <span className="rating-count">
                          {activeCard.review_count.toLocaleString('vi-VN')} lượt đánh giá trên dienmayxanh.com
                        </span>
                      )}
                    </div>
                  )}
                  {activeCard.reviews.map((r, i) => (
                    <div className="modal-review-item" key={i}>
                      <div className="review-head">
                        <span className="review-author">{r.author || 'Khách hàng'}</span>
                        {r.rating != null && <span className="review-stars">{'★'.repeat(Math.round(r.rating))}{'☆'.repeat(5 - Math.round(r.rating))}</span>}
                      </div>
                      <p className="review-content">{r.content}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="modal-promos-list">
                  {activeCard.promos.map((p, i) => (
                    <div className="modal-promo-item" key={i}>
                      <svg viewBox="0 0 20 20" fill="currentColor" className="gift-icon">
                        <path fillRule="evenodd" d="M5 5a3 3 0 015-2.236A3 3 0 0115 5h2a1 1 0 011 1v3a1 1 0 01-1 1h-1v5a2 2 0 01-2 2H6a2 2 0 01-2-2V10H3a1 1 0 01-1-1V6a1 1 0 011-1h2zm3-.882a1 1 0 00-.832.882H9.9A1 1 0 009 4.118zM11 5h1.732a1 1 0 00-.832-.882A1 1 0 0011 5zm-7 3v1h12V8H4zm1 2v5a1 1 0 001 1h3v-6H5zm6 6h3a1 1 0 001-1v-5h-4v6z" clipRule="evenodd" />
                      </svg>
                      <span>{p}</span>
                    </div>
                  ))}
                  {activeCard.promos.length === 0 && <p className="empty-text">Không có quà tặng, khuyến mãi kèm theo.</p>}
                </div>
              )}
            </div>
            
            {activeCard.product_link && (
              <div className="modal-footer">
                <a href={activeCard.product_link} target="_blank" rel="noopener noreferrer" className="modal-action-btn">
                  Mua tại Điện Máy Xanh &rarr;
                </a>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
