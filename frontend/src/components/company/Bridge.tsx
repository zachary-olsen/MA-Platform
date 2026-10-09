import type { Valuation } from '../../lib/api'
import { pct, price as fmtPrice, usd } from '../../lib/format'

/** EV to value per share, written out as a column of arithmetic. */
export function Bridge({ b }: { b: Valuation['bridge'] }) {
  const lines: [string, string, string?][] = [
    ['PV of forecast cash flows', usd(b.pv_explicit, 2)],
    ['PV of terminal value', usd(b.pv_terminal, 2), `${pct(b.terminal_share, 0)} of EV`],
    ['Enterprise value', usd(b.enterprise_value, 2), 'total'],
    ['− Net debt', usd(b.net_debt, 2)],
    ['− Minority interest & preferred', usd(b.other_claims, 2)],
    ['Equity value', usd(b.equity_value, 2), 'total'],
    ['÷ Shares outstanding', `${(b.shares / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1 })}M`],
  ]
  return (
    <div className="bridge">
      <span className="kicker">From enterprise value to a share price</span>
      <table className="bridge-table">
        <tbody>
          {lines.map(([label, value, extra]) => (
            <tr key={label} className={extra === 'total' ? 'total' : undefined}>
              <td>
                {label}
                {extra && extra !== 'total' && <span className="ledger-note">{extra}</span>}
              </td>
              <td className="num">{value}</td>
            </tr>
          ))}
          <tr className="result">
            <td>DCF value per share</td>
            <td className="num">{fmtPrice(b.value_per_share)}</td>
          </tr>
          <tr>
            <td className="muted">vs current price {fmtPrice(b.price)}</td>
            <td className="num">{pct(b.upside, 0, true)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}
