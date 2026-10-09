import { useState } from 'react'
import type { FootballBar } from '../../lib/api'
import { price as fmtPrice } from '../../lib/format'
import { niceTicks, useWidth } from '../../lib/useWidth'

const ROW_H = 50
const TOP = 30
const AXIS_H = 26
const BAR_H = 10
const END_PAD = 56

/**
 * Valuation ranges per method on one price-per-share axis, cut by the current price.
 * Every value is printed (range ends, label detail), so hover only adds the midpoint.
 */
export function FootballField({ bars, price }: { bars: FootballBar[]; price: number }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [active, setActive] = useState<number | null>(null)

  const LABEL_W = width < 560 ? 120 : 168
  const lo = Math.min(price, ...bars.map((b) => b.low))
  const hi = Math.max(price, ...bars.map((b) => b.high))
  const pad = (hi - lo) * 0.06 || 1
  const domain = [lo - pad, hi + pad]
  const plotLeft = LABEL_W + END_PAD
  const plotRight = Math.max(width - END_PAD, plotLeft + 100)
  const x = (v: number) => plotLeft + ((v - domain[0]) / (domain[1] - domain[0])) * (plotRight - plotLeft)
  const height = TOP + bars.length * ROW_H + AXIS_H
  const ticks = niceTicks(domain[0], domain[1], Math.max(3, Math.floor((plotRight - plotLeft) / 90)))
  const hovered = active === null ? null : bars[active]

  return (
    <div ref={ref} className="chart football">
      <svg width={width} height={height} role="img" aria-label="Valuation ranges by method versus current price">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={TOP - 6} y2={height - AXIS_H} className="grid-line" />
            <text x={x(t)} y={height - 8} className="axis-label" textAnchor="middle">
              {fmtPrice(t).replace(/\.00$/, '')}
            </text>
          </g>
        ))}
        {domain[0] < 0 && domain[1] > 0 && (
          <line x1={x(0)} x2={x(0)} y1={TOP - 6} y2={height - AXIS_H} className="zero-line" />
        )}

        {bars.map((bar, i) => {
          const y = TOP + i * ROW_H
          const cy = y + ROW_H / 2
          return (
            <g
              key={bar.method}
              tabIndex={0}
              role="listitem"
              aria-label={`${bar.method}: ${fmtPrice(bar.low)} to ${fmtPrice(bar.high)}`}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              className="ff-row"
            >
              <rect x={0} y={y} width={width} height={ROW_H} className={active === i ? 'row-hit active' : 'row-hit'} />
              <text x={0} y={cy - 3} className="ff-method">
                {bar.method}
              </text>
              {bar.detail && (
                <text x={0} y={cy + 12} className="ff-detail">
                  {bar.detail}
                </text>
              )}
              <rect
                x={x(bar.low)}
                y={cy - BAR_H / 2}
                width={Math.max(x(bar.high) - x(bar.low), 2)}
                height={BAR_H}
                rx={2}
                className={`ff-bar ff-${bar.kind}`}
              />
              {bar.mid !== null && (
                <rect x={x(bar.mid) - 1} y={cy - BAR_H / 2} width={2} height={BAR_H} className="ff-mid" />
              )}
              <text x={x(bar.low) - 6} y={cy + 4} className="ff-value" textAnchor="end">
                {fmtPrice(bar.low)}
              </text>
              <text x={x(bar.high) + 6} y={cy + 4} className="ff-value">
                {fmtPrice(bar.high)}
              </text>
            </g>
          )
        })}

        <line x1={x(price)} x2={x(price)} y1={TOP - 10} y2={height - AXIS_H} className="price-line" />
        <text x={x(price)} y={TOP - 16} className="price-label" textAnchor="middle">
          Price {fmtPrice(price)}
        </text>
      </svg>

      {hovered && active !== null && (
        <div
          className="tooltip"
          style={{
            left: Math.min(x((hovered.low + hovered.high) / 2), width - 240),
            top: TOP + active * ROW_H + ROW_H - 4,
          }}
        >
          <div className="tooltip-title">{hovered.method}</div>
          <div className="num">
            <strong>{fmtPrice(hovered.low)}</strong> – <strong>{fmtPrice(hovered.high)}</strong>
            {hovered.mid !== null && (
              <span className="muted"> · {hovered.kind === 'monte_carlo' ? 'median' : 'base'} {fmtPrice(hovered.mid)}</span>
            )}
          </div>
          {hovered.detail && <div className="muted">{hovered.detail}</div>}
          <div className="muted num">
            vs price: {(((hovered.low / price) - 1) * 100).toFixed(0)}% to {(((hovered.high / price) - 1) * 100).toFixed(0)}%
          </div>
        </div>
      )}
    </div>
  )
}
