import type { Valuation } from '../../lib/api'
import { pct, price as fmtPrice } from '../../lib/format'

const STEPS = 5

/** WACC x terminal growth grid. One-hue ramp by value; bold = above current price. */
export function SensitivityGrid({ grid, price, base }: { grid: Valuation['sensitivity']; price: number; base: number }) {
  const values = grid.values.flat().filter((v): v is number => v !== null)
  const sorted = [...values].sort((a, b) => a - b)
  const step = (v: number) => {
    const rank = sorted.findIndex((s) => s >= v)
    return Math.min(STEPS, Math.floor((rank / Math.max(sorted.length - 1, 1)) * STEPS) + 1)
  }
  const mid = Math.floor(grid.waccs.length / 2)

  return (
    <div>
      <table className="heat" aria-label="Value per share by WACC and terminal growth">
        <thead>
          <tr>
            <th className="corner">
              <span>WACC ↓</span>
              <span>g →</span>
            </th>
            {grid.growths.map((g) => (
              <th key={g} scope="col" className="num">
                {pct(g, 1)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.waccs.map((w, i) => (
            <tr key={w}>
              <th scope="row" className="num">
                {pct(w, 1)}
              </th>
              {grid.values[i].map((v, j) => {
                const isBase = i === mid && j === mid
                const above = v !== null && v >= price
                return (
                  <td
                    key={j}
                    className={`num seq-${v === null ? 0 : step(v)}${isBase ? ' base' : ''}${above ? ' above' : ''}`}
                    title={v === null ? 'WACC must exceed growth' : `${fmtPrice(v)} per share at WACC ${pct(w)} and g ${pct(grid.growths[j])}${above ? ' (above current price)' : ''}`}
                  >
                    {v === null ? '—' : fmtPrice(v)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">
        Darker is higher. Outlined: base case ({fmtPrice(base)}). <strong>Bold</strong>: at or
        above the current price of {fmtPrice(price)}.
      </p>
    </div>
  )
}
