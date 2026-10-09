import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AssumptionLedger } from '../components/company/AssumptionLedger'
import { Bridge } from '../components/company/Bridge'
import { CompsSection } from '../components/company/CompsSection'
import { FootballField } from '../components/company/FootballField'
import { HistorySection } from '../components/company/HistorySection'
import { MonteCarloChart } from '../components/company/MonteCarloChart'
import { SensitivityGrid } from '../components/company/SensitivityGrid'
import { api, type FootballBar } from '../lib/api'
import { displayName, multiple, pct, price as fmtPrice, usd } from '../lib/format'
import { updateParams, useRoute } from '../lib/router'

// URL parameter -> API query parameter for DCF overrides
const OVERRIDES: Record<string, string> = { g: 'growth', m: 'margin', w: 'wacc', tg: 'terminal_growth' }

/** One plain sentence placing the price against the valuation ranges. */
function verdict(bars: FootballBar[], price: number): string {
  const methods = bars.filter((b) => b.kind !== 'market')
  const n = methods.length
  if (n === 0) return ''
  const above = methods.filter((b) => price > b.high).length
  const below = methods.filter((b) => price < b.low).length
  const inside = n - above - below
  const at = `At ${fmtPrice(price)}, the share price`
  if (above === n)
    return `${at} is above all ${n} valuation ranges: the market is paying for more growth or margin than these defaults assume.`
  if (below === n)
    return `${at} is below all ${n} valuation ranges: the market doubts the history these defaults extrapolate will repeat.`
  const parts = [
    above && `above ${above}`,
    inside && `inside ${inside}`,
    below && `below ${below}`,
  ].filter(Boolean)
  return `${at} sits ${parts.join(', ')} of the ${n} valuation ranges.`
}

export function CompanyPage({ ticker, asOf }: { ticker: string; asOf: string | null }) {
  const { params } = useRoute()
  const overrides = Object.fromEntries(
    Object.entries(OVERRIDES).map(([param, apiName]) => [apiName, params.get(param) ?? undefined]),
  )

  const detail = useQuery({ queryKey: ['company', ticker, asOf], queryFn: () => api.company(ticker, asOf) })
  const comps = useQuery({ queryKey: ['comps', ticker, asOf], queryFn: () => api.comps(ticker, asOf) })
  const valuation = useQuery({
    queryKey: ['valuation', ticker, asOf, overrides],
    queryFn: () => api.valuation(ticker, asOf, overrides),
    placeholderData: keepPreviousData,
  })

  const setOverride = (param: string, value: number | null) =>
    updateParams((p) => (value === null ? p.delete(param) : p.set(param, String(Number(value.toFixed(4))))))
  const resetOverrides = () => updateParams((p) => Object.keys(OVERRIDES).forEach((k) => p.delete(k)))

  if (detail.error) {
    return <p className="error">{(detail.error as Error).message}</p>
  }
  const d = detail.data
  const snap = d?.snapshot
  const v = valuation.data
  const latest = d?.statements.at(-1)

  return (
    <article>
      <header className="company-head grid">
        <div className="span-7">
          <div className="kicker">
            {d ? `${d.peer_group} · ${d.sector}` : ' '}
          </div>
          <h1 className="company-title">{d ? displayName(d.name) : ticker}</h1>
          {d && (
            <div className="company-meta">
              <span className="ticker">{d.ticker}</span>
              <span>CIK {d.cik}</span>
              {latest && (
                <span>
                  Latest 10-K: FY{latest.fiscal_year}, filed {latest.filed_date}
                </span>
              )}
              {(d.flags.warning ?? 0) + (d.flags.error ?? 0) > 0 && (
                <span title="Automated accounting checks on this company's filings data">
                  {(d.flags.error ?? 0) + (d.flags.warning ?? 0)} data-quality{' '}
                  {(d.flags.error ?? 0) + (d.flags.warning ?? 0) === 1 ? 'warning' : 'warnings'} across all filings
                </span>
              )}
            </div>
          )}
        </div>
        <div className="span-5 quote">
          {snap ? (
            <>
              <div className="quote-price">
                <span className="quote-figure">{fmtPrice(snap.price)}</span>
                <span className="muted"> USD on {snap.price_date}</span>
              </div>
              <dl className="quote-stats">
                <div>
                  <dt>Market cap</dt>
                  <dd className="num">{usd(snap.market_cap)}</dd>
                </div>
                <div>
                  <dt>EV</dt>
                  <dd className="num">{usd(snap.ev)}</dd>
                </div>
                <div>
                  <dt>EV/EBITDA</dt>
                  <dd className="num">{multiple(snap.ev_ebitda)}</dd>
                </div>
                <div>
                  <dt>Rev growth</dt>
                  <dd className="num">{pct(snap.revenue_growth)}</dd>
                </div>
              </dl>
            </>
          ) : (
            d && <p className="note">No price on this date, so no valuation.</p>
          )}
        </div>
      </header>

      {valuation.error && <p className="error">{(valuation.error as Error).message}</p>}

      {v && (
        <>
          <p className="verdict serif">{verdict(v.football_field.bars, v.football_field.price)}</p>
          <div className={`grid valuation${valuation.isPlaceholderData ? ' is-fetching' : ''}`}>
            <aside className="span-4">
              <AssumptionLedger assumptions={v.assumptions} onOverride={setOverride} onReset={resetOverrides} />
              <Bridge b={v.bridge} />
            </aside>
            <section className="span-8">
              <div className="panel-head">
                <h2 className="section-title">Valuation ranges</h2>
                <span className="note">Price per share, from FY{v.fiscal_year} fundamentals</span>
              </div>
              <FootballField bars={v.football_field.bars} price={v.football_field.price} />

              <div className="grid nested">
                <div className="span-7">
                  <div className="panel-head">
                    <h3 className="sub-title">DCF sensitivity</h3>
                  </div>
                  <SensitivityGrid grid={v.sensitivity} price={v.bridge.price} base={v.bridge.value_per_share} />
                </div>
                <div className="span-5">
                  <div className="panel-head">
                    <h3 className="sub-title">Monte Carlo</h3>
                  </div>
                  {v.monte_carlo ? (
                    <>
                      <MonteCarloChart mc={v.monte_carlo} price={v.bridge.price} />
                      <p className="note">
                        {(v.monte_carlo.draws - v.monte_carlo.discarded).toLocaleString()} draws of
                        growth (sd {pct(v.monte_carlo.growth_volatility)}), margin (sd{' '}
                        {pct(v.monte_carlo.margin_volatility)}), WACC and terminal growth.{' '}
                        {pct(v.monte_carlo.prob_above_price, 0)} land above today's price. Chart
                        omits the outer 1% tails.
                      </p>
                    </>
                  ) : (
                    <p className="note">Monte Carlo off.</p>
                  )}
                </div>
              </div>
            </section>
          </div>
        </>
      )}
      {!v && !valuation.error && snap && <p className="serif muted loading">Running the DCF and 10,000 simulations…</p>}

      {comps.data && (
        <>
          <div className="section-head">
            <h2 className="section-title">Comparable companies</h2>
            <span className="note">Latest annual figures and prices as of {comps.data.as_of}</span>
          </div>
          <CompsSection comps={comps.data} asOf={asOf} />
        </>
      )}

      {d && d.metrics.length > 0 && (
        <>
          <div className="section-head">
            <h2 className="section-title">Financial history</h2>
            <span className="note">As reported in 10-K filings public by {d.as_of}</span>
          </div>
          <HistorySection metrics={d.metrics} />
        </>
      )}
    </article>
  )
}
