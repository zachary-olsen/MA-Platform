import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { CompanySearch } from './components/CompanySearch'
import { api } from './lib/api'
import { longDate } from './lib/format'
import { navigate, updateParams, useRoute } from './lib/router'
import { ScreenerPage } from './pages/ScreenerPage'

// The company page carries the charting library; load it only when needed.
const CompanyPage = lazy(() => import('./pages/CompanyPage').then((m) => ({ default: m.CompanyPage })))

export default function App() {
  const { path, params } = useRoute()
  const asOf = params.get('as_of')
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const companyMatch = path.match(/^\/c\/([A-Za-z0-9.-]+)$/)

  const goHome = () => {
    const next = new URLSearchParams()
    if (asOf) next.set('as_of', asOf)
    navigate('/', next)
  }

  return (
    <div className="page">
      <header className="masthead">
        <div>
          <a
            className="wordmark"
            href="#/"
            onClick={(e) => {
              e.preventDefault()
              goHome()
            }}
          >
            M&amp;A Platform <em>— target screening &amp; valuation</em>
          </a>
          <div className="masthead-sub">
            {meta.data
              ? `${meta.data.company_count} listed US healthcare and semiconductor companies${
                  meta.data.acquired_count ? ` plus ${meta.data.acquired_count} since acquired` : ''
                } · financials from SEC filings, as known on each date`
              : 'US healthcare and semiconductor companies · financials from SEC filings'}
          </div>
        </div>
        <div className="masthead-controls">
          <label>
            <span className="field-label">Data as of</span>
            <input
              className="text-input num"
              type="date"
              min="2014-01-01"
              max={meta.data?.latest_price_date ?? undefined}
              value={asOf ?? meta.data?.latest_price_date ?? ''}
              onChange={(e) =>
                updateParams((p) => {
                  if (e.target.value && e.target.value !== meta.data?.latest_price_date) {
                    p.set('as_of', e.target.value)
                  } else {
                    p.delete('as_of')
                  }
                })
              }
            />
          </label>
          {asOf && (
            <button className="link-button" onClick={() => updateParams((p) => p.delete('as_of'))}>
              Back to today
            </button>
          )}
          <CompanySearch asOf={asOf} />
        </div>
      </header>

      {asOf && (
        <p className="note" style={{ marginTop: -20, marginBottom: 28 }}>
          Viewing the market as it stood on <strong>{longDate(asOf)}</strong>: only filings,
          prices and share counts public by then are used.
        </p>
      )}

      {companyMatch ? (
        <Suspense fallback={<p className="serif muted loading">Opening…</p>}>
          <CompanyPage ticker={companyMatch[1].toUpperCase()} asOf={asOf} />
        </Suspense>
      ) : (
        <ScreenerPage asOf={asOf} meta={meta.data} />
      )}

      <footer className="colophon">
        <div>
          A research and learning project, not investment advice. Valuations are mechanical
          outputs of stated assumptions and will be wrong in ways the assumptions don't capture.
        </div>
        <div>
          Sources: SEC EDGAR XBRL company facts (financials, share counts), Yahoo Finance via
          yfinance (prices, unofficial), FRED DGS10 (10-year Treasury). Fundamentals are annual
          10-K figures; multiples can lag the latest quarter.
        </div>
      </footer>
    </div>
  )
}
