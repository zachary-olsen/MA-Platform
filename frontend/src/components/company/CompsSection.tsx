import type { CompsResponse, Row } from '../../lib/api'
import { displayName, multiple, pct, price as fmtPrice, usd } from '../../lib/format'
import { navigate } from '../../lib/router'

const PEER_COLUMNS: [string, string, (v: Row[string]) => string][] = [
  ['market_cap', 'Mkt cap', (v) => usd(v)],
  ['revenue', 'Revenue', (v) => usd(v)],
  ['revenue_growth', 'Growth', (v) => pct(v)],
  ['ebitda_margin', 'EBITDA mgn', (v) => pct(v)],
  ['ev_revenue', 'EV/Rev', (v) => multiple(v)],
  ['ev_ebitda', 'EV/EBITDA', (v) => multiple(v)],
  ['ev_ebit', 'EV/EBIT', (v) => multiple(v)],
  ['pe', 'P/E', (v) => multiple(v)],
]

export function CompsSection({ comps, asOf }: { comps: CompsResponse; asOf: string | null }) {
  const open = (ticker: string) => {
    const params = new URLSearchParams()
    if (asOf) params.set('as_of', asOf)
    navigate(`/c/${ticker}`, params)
    window.scrollTo({ top: 0 })
  }
  const rows = [{ row: comps.target, self: true }, ...comps.peers.map((row) => ({ row, self: false }))]

  return (
    <div className="grid">
      <div className="span-4">
        <table className="data">
          <thead>
            <tr>
              <th className="left">Multiple</th>
              <th>Company</th>
              <th>Peer median</th>
              <th>Implied price</th>
            </tr>
          </thead>
          <tbody>
            {comps.multiples.map((m) => (
              <tr key={m.multiple}>
                <td className="left">{m.label}</td>
                <td className="num">
                  {multiple(m.company)}
                  {m.vs_peers !== null && <span className="ledger-note">{pct(m.vs_peers, 0, true)} vs peers</span>}
                </td>
                <td className="num">{multiple(m.peer_median)}</td>
                <td className="num">{fmtPrice(m.implied_price)}</td>
              </tr>
            ))}
            <tr className="self">
              <td className="left">Median implied</td>
              <td />
              <td />
              <td className="num">
                {fmtPrice(comps.implied_price_median)}
                <span className="ledger-note">{pct(comps.upside, 0, true)} vs price</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p className="note">
          Peer medians exclude the company itself. Multiples on negative earnings are left
          blank rather than counted as cheap. Implied price = peer median × the company's own
          metric, less net debt and other claims for EV multiples.
        </p>
      </div>
      <div className="span-8 table-wrap peers-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="left">{comps.peer_group} ({comps.peers.length} peers)</th>
              {PEER_COLUMNS.map(([key, label]) => (
                <th key={key}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ row: r, self }) => (
              <tr
                key={String(r.ticker)}
                className={self ? 'self' : 'clickable'}
                tabIndex={self ? undefined : 0}
                onClick={self ? undefined : () => open(String(r.ticker))}
                onKeyDown={self ? undefined : (e) => e.key === 'Enter' && open(String(r.ticker))}
              >
                <td className="left">
                  <span className="ticker">{r.ticker}</span>{' '}
                  <span className="inline-name">{displayName(String(r.name))}</span>
                </td>
                {PEER_COLUMNS.map(([key, , fmt]) => (
                  <td key={key} className="num">
                    {fmt(r[key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
