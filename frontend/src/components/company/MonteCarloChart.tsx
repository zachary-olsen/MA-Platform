import { useState } from 'react'
import type { Valuation } from '../../lib/api'
import { price as fmtPrice } from '../../lib/format'
import { niceTicks, useWidth } from '../../lib/useWidth'

const HEIGHT = 190
const TOP = 26
const BOTTOM = 62
const SIDE = 8

/** Histogram of per-share values across draws; P10/P50/P90 bracket and price line. */
export function MonteCarloChart({ mc, price }: { mc: NonNullable<Valuation['monte_carlo']>; price: number }) {
  const [ref, width] = useWidth<HTMLDivElement>(420)
  const [active, setActive] = useState<number | null>(null)
  const bins = mc.histogram
  const kept = mc.draws - mc.discarded
  const lo = Math.min(bins[0].x0, price)
  const hi = Math.max(bins[bins.length - 1].x1, price)
  const x = (v: number) => SIDE + ((v - lo) / (hi - lo)) * (width - 2 * SIDE)
  const maxCount = Math.max(...bins.map((b) => b.count))
  const plotBottom = HEIGHT - BOTTOM
  const y = (c: number) => plotBottom - (c / maxCount) * (plotBottom - TOP)
  const p = mc.percentiles
  const bracketY = plotBottom + 30
  const ticks = niceTicks(lo, hi, Math.max(4, Math.floor(width / 70)))
  const hovered = active === null ? null : bins[active]

  return (
    <div ref={ref} className="chart">
      <svg width={width} height={HEIGHT} role="img" aria-label="Distribution of Monte Carlo values per share">
        <line x1={SIDE} x2={width - SIDE} y1={plotBottom} y2={plotBottom} className="axis-line" />
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={plotBottom + 14} className="axis-label" textAnchor="middle">
            {fmtPrice(t).replace(/\.00$/, '')}
          </text>
        ))}
        {bins.map((b, i) => (
          <g key={i} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)}>
            <rect x={x(b.x0)} y={TOP} width={Math.max(x(b.x1) - x(b.x0), 1)} height={plotBottom - TOP} className="hit" />
            <rect
              x={x(b.x0) + 0.5}
              y={y(b.count)}
              width={Math.max(x(b.x1) - x(b.x0) - 1, 1)}
              height={plotBottom - y(b.count)}
              className={active === i ? 'hist-bar active' : 'hist-bar'}
            />
          </g>
        ))}

        <line x1={x(p.p10)} x2={x(p.p90)} y1={bracketY} y2={bracketY} className="bracket" />
        {/* P10 and P90 hang outward from the bracket ends; P50 sits on a second line so
            the three labels never collide however narrow the range is. */}
        {(
          [
            ['p10', 'end', 0],
            ['p90', 'start', 0],
            ['p50', 'middle', 13],
          ] as const
        ).map(([k, anchor, dy]) => (
          <g key={k}>
            <line x1={x(p[k])} x2={x(p[k])} y1={bracketY - 4} y2={bracketY + 4} className="bracket" />
            <text
              x={x(p[k]) + (anchor === 'end' ? -4 : anchor === 'start' ? 4 : 0)}
              y={bracketY + (dy ? 17 + dy : 4)}
              className="axis-label strong"
              textAnchor={anchor}
            >
              {k.toUpperCase()} {fmtPrice(p[k])}
            </text>
          </g>
        ))}

        <line x1={x(price)} x2={x(price)} y1={TOP - 8} y2={plotBottom} className="price-line" />
        <text x={x(price)} y={TOP - 13} className="price-label" textAnchor={x(price) > width - 60 ? 'end' : 'middle'}>
          Price {fmtPrice(price)}
        </text>
      </svg>
      {hovered && active !== null && (
        <div className="tooltip" style={{ left: Math.min(x(hovered.x0), width - 200), top: TOP + 4 }}>
          <div className="num">
            <strong>{hovered.count.toLocaleString()}</strong> draws ({((hovered.count / kept) * 100).toFixed(1)}%)
          </div>
          <div className="muted num">
            {fmtPrice(hovered.x0)} – {fmtPrice(hovered.x1)} per share
          </div>
        </div>
      )}
    </div>
  )
}
