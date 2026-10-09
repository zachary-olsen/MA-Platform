import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  tableFeatures,
  useTable,
} from '@tanstack/react-table'
import { useMemo } from 'react'
import type { FieldInfo, Row } from '../lib/api'
import { byFormat, displayName, isNum, pct } from '../lib/format'
import { navigate } from '../lib/router'

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { basic: sortFn_basic, alphanumeric: sortFn_alphanumeric },
})
const helper = createColumnHelper<typeof features, Row>()

// Columns shown in the results table, in order. Everything else stays on the company page.
const COLUMNS = [
  'market_cap',
  'ev_ebitda',
  'ev_ebitda_vs_peers',
  'revenue_growth',
  'ebitda_margin',
  'fcf_margin',
  'roic',
  'net_debt_to_ebitda',
] as const

function ScoreCell({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>
  return (
    <span className="score">
      <span className="num">{value.toFixed(2)}</span>
      <span className="score-track" aria-hidden>
        <span className="score-fill" style={{ width: `${value * 100}%` }} />
      </span>
    </span>
  )
}

export function ScreenTable({
  rows,
  fields,
  asOf,
}: {
  rows: Row[]
  fields: Map<string, FieldInfo>
  asOf: string | null
}) {
  const columns = useMemo(
    () =>
      helper.columns([
        helper.accessor((r) => r.ticker as string, {
          id: 'company',
          header: 'Company',
          sortFn: 'alphanumeric',
          cell: (info) => (
            <>
              <span className="ticker">{info.row.original.ticker}</span>
              <span className="company-name">
                {displayName(String(info.row.original.name))}
                <span className="peer-tag"> · {info.row.original.peer_group}</span>
              </span>
            </>
          ),
          meta: { align: 'left' },
        }),
        ...COLUMNS.map((name) => {
          const field = fields.get(name)
          return helper.accessor((r) => (r[name] ?? undefined) as number | string | undefined, {
            id: name,
            header: field?.label ?? name,
            sortFn: field?.format === 'text' ? 'alphanumeric' : 'basic',
            sortUndefined: 'last',
            cell: (info) => {
              const v = info.row.original[name]
              if (name === 'ev_ebitda_vs_peers') return pct(v, 0, true)
              return byFormat(v, field?.format ?? 'text')
            },
            meta: { align: field?.format === 'text' ? 'left' : 'right', title: field?.description },
          })
        }),
        helper.accessor((r) => (isNum(r.score) ? r.score : undefined), {
          id: 'score',
          header: 'Score',
          sortFn: 'basic',
          sortUndefined: 'last',
          cell: (info) => <ScoreCell value={isNum(info.row.original.score) ? info.row.original.score : null} />,
        }),
      ]),
    [fields],
  )

  const table = useTable({
    features,
    columns,
    data: rows,
    initialState: { sorting: [{ id: 'score', desc: true }] },
  })

  const open = (ticker: string) => {
    const params = new URLSearchParams()
    if (asOf) params.set('as_of', asOf)
    navigate(`/c/${ticker}`, params)
  }

  if (rows.length === 0) {
    return (
      <p className="empty serif">
        Nothing passes. Loosen a filter on the left; filters treat missing data as a fail.
      </p>
    )
  }

  return (
    <div className="table-wrap">
      <table className="data screen-table">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const meta = header.column.columnDef.meta as { align?: string; title?: string } | undefined
                const sorted = header.column.getIsSorted()
                return (
                  <th
                    key={header.id}
                    className={meta?.align === 'left' ? 'left' : undefined}
                    title={meta?.title}
                    aria-sort={sorted ? (sorted === 'desc' ? 'descending' : 'ascending') : undefined}
                  >
                    <button
                      className="sort-button"
                      data-sorted={sorted || undefined}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      <table.FlexRender header={header} />
                      <span className="sort-mark" aria-hidden>
                        {sorted === 'desc' ? ' ↓' : sorted === 'asc' ? ' ↑' : ''}
                      </span>
                    </button>
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className="clickable"
              tabIndex={0}
              onClick={() => open(row.original.ticker as string)}
              onKeyDown={(e) => e.key === 'Enter' && open(row.original.ticker as string)}
            >
              {row.getAllCells().map((cell) => {
                const meta = cell.column.columnDef.meta as { align?: string } | undefined
                const left = meta?.align === 'left'
                return (
                  <td key={cell.id} className={left ? 'left' : 'num'}>
                    <table.FlexRender cell={cell} />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
