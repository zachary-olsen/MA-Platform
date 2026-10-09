import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Row } from '../../lib/api'
import { isNum, multiple, pct, usd } from '../../lib/format'

const INK = '#3b3a33'
const GRID = '#e6e1d5'
const AXIS = { fontFamily: 'Geist Mono, monospace', fontSize: 11, fill: '#6e695d' }

const TABLE_ROWS: [string, string, (v: Row[string]) => string][] = [
  ['revenue', 'Revenue', (v) => usd(v)],
  ['revenue_growth', 'Revenue growth', (v) => pct(v)],
  ['gross_margin', 'Gross margin', (v) => pct(v)],
  ['operating_margin', 'EBIT margin', (v) => pct(v)],
  ['ebitda', 'EBITDA', (v) => usd(v)],
  ['fcf', 'Free cash flow', (v) => usd(v)],
  ['fcf_margin', 'FCF margin', (v) => pct(v)],
  ['roic', 'ROIC', (v) => pct(v)],
  ['net_debt', 'Net debt', (v) => usd(v)],
  ['net_debt_to_ebitda', 'Net debt / EBITDA', (v) => multiple(v)],
]

function ChartTooltip({ active, payload, label, format }: {
  active?: boolean
  payload?: { value?: number }[]
  label?: string | number
  format: (v: number) => string
}) {
  if (!active || !payload?.length || !isNum(payload[0].value ?? null)) return null
  return (
    <div className="tooltip static">
      <div className="num"><strong>{format(payload[0].value as number)}</strong></div>
      <div className="muted">FY{label}</div>
    </div>
  )
}

/** Annual history as known on the as-of date: a table plus two single-measure charts
 * (separate charts rather than one dual-axis plot). */
export function HistorySection({ metrics }: { metrics: Row[] }) {
  const recent = metrics.slice(-6)
  const data = recent.map((m) => ({
    fy: m.fiscal_year as number,
    revenue: isNum(m.revenue) ? m.revenue / 1e9 : null,
    margin: isNum(m.operating_margin) ? m.operating_margin * 100 : null,
  }))

  return (
    <div className="grid">
      <div className="span-7 table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="left">Fiscal year</th>
              {recent.map((m) => (
                <th key={String(m.fiscal_year)}>FY{m.fiscal_year}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {TABLE_ROWS.map(([key, label, fmt]) => (
              <tr key={key}>
                <td className="left">{label}</td>
                {recent.map((m) => (
                  <td key={String(m.fiscal_year)} className="num">
                    {fmt(m[key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="span-5 small-multiples">
        <figure>
          <figcaption className="kicker">Revenue, $B</figcaption>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -12 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="fy" tick={AXIS} tickLine={false} axisLine={{ stroke: '#d9d3c5' }} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} />
              <Tooltip cursor={{ fill: '#ece8de' }} content={<ChartTooltip format={(v) => `$${v.toFixed(2)}B`} />} />
              <Bar dataKey="revenue" fill={INK} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </figure>
        <figure>
          <figcaption className="kicker">EBIT margin, %</figcaption>
          <ResponsiveContainer width="100%" height={150}>
            <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="fy" tick={AXIS} tickLine={false} axisLine={{ stroke: '#d9d3c5' }} padding={{ left: 12, right: 12 }} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} />
              <Tooltip cursor={{ stroke: '#9a9485' }} content={<ChartTooltip format={(v) => `${v.toFixed(1)}%`} />} />
              <Line
                dataKey="margin"
                stroke={INK}
                strokeWidth={2}
                dot={{ r: 4, fill: INK, stroke: '#f4f1ea', strokeWidth: 2 }}
                activeDot={{ r: 5, fill: INK, stroke: '#f4f1ea', strokeWidth: 2 }}
                connectNulls
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </figure>
      </div>
    </div>
  )
}
