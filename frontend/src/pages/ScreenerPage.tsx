import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ScreenTable } from '../components/ScreenTable'
import { api, type FieldInfo, type FilterInfo, type Meta } from '../lib/api'
import { byFormat, OP_SYMBOL } from '../lib/format'
import { updateParams, useRoute } from '../lib/router'

const NO_FILTERS = 'none'

function serialize(f: FilterInfo) {
  return `${f.field}${f.op}${f.value}`
}

function parseFilter(text: string): FilterInfo | null {
  const m = text.match(/^(\w+)(>=|<=|>|<)(-?[\d.e+-]+)$/)
  return m ? { field: m[1], op: m[2], value: Number(m[3]) } : null
}

/** Value as typed by a person -> raw units (percent and $B inputs). */
function toRaw(input: number, format: FieldInfo['format']) {
  if (format === 'pct') return input / 100
  if (format === 'usd') return input * 1e9
  return input
}

const UNIT_HINT: Partial<Record<FieldInfo['format'], string>> = { pct: '%', usd: '$B', multiple: '×' }

export function ScreenerPage({ asOf, meta }: { asOf: string | null; meta: Meta | undefined }) {
  const { params } = useRoute()
  const presetName = params.get('preset') ?? 'target'
  const preset = meta?.presets.find((p) => p.name === presetName) ?? meta?.presets[0]
  const sectors = params.getAll('sector')

  const filters: FilterInfo[] = useMemo(() => {
    const raw = params.getAll('f')
    if (raw.length === 0) return preset?.filters ?? []
    return raw.filter((r) => r !== NO_FILTERS).map(parseFilter).filter((f) => f !== null)
  }, [params, preset])

  const fields = useMemo(() => new Map(meta?.fields.map((f) => [f.name, f]) ?? []), [meta])

  const screen = useQuery({
    queryKey: ['screen', asOf, presetName, filters.map(serialize), sectors],
    queryFn: () => api.screen(asOf, presetName, filters.map(serialize), sectors),
    enabled: Boolean(meta),
    placeholderData: keepPreviousData,
  })

  const setFilters = (next: FilterInfo[]) =>
    updateParams((p) => {
      p.delete('f')
      if (next.length === 0) p.append('f', NO_FILTERS)
      next.forEach((f) => p.append('f', serialize(f)))
    })

  const choosePreset = (name: string) =>
    updateParams((p) => {
      p.set('preset', name)
      p.delete('f')
    })

  // No `sector` param means every sector is shown (all boxes checked).
  const toggleSector = (name: string) =>
    updateParams((p) => {
      const all = meta?.sectors.map((s) => s.name) ?? []
      const current = p.getAll('sector').length ? p.getAll('sector') : all
      const next = current.includes(name) ? current.filter((s) => s !== name) : [...current, name]
      p.delete('sector')
      if (next.length > 0 && next.length < all.length) next.forEach((s) => p.append('sector', s))
    })

  return (
    <div className="grid">
      <aside className="span-3 rail">
        <div className="kicker">The screen</div>
        <nav className="preset-tabs" aria-label="Screen presets">
          {meta?.presets.map((p) => (
            <button
              key={p.name}
              className="preset-tab"
              aria-pressed={p.name === preset?.name}
              onClick={() => choosePreset(p.name)}
            >
              {p.name}
            </button>
          ))}
        </nav>
        {preset && <p className="preset-description serif">{preset.description}.</p>}

        <div className="rail-block">
          <div className="rail-heading">
            <span className="kicker">Must pass</span>
            {params.getAll('f').length > 0 && (
              <button className="link-button" onClick={() => choosePreset(presetName)}>
                Reset to preset
              </button>
            )}
          </div>
          <ul className="filter-lines">
            {filters.map((f, i) => {
              const info = fields.get(f.field)
              return (
                <li key={`${serialize(f)}-${i}`}>
                  <span>
                    {info?.label ?? f.field}{' '}
                    <span className="op">{OP_SYMBOL[f.op] ?? f.op}</span>{' '}
                    <span className="num">{byFormat(f.value, info?.format ?? 'number')}</span>
                  </span>
                  <button
                    className="remove"
                    aria-label={`Remove filter ${info?.label ?? f.field}`}
                    onClick={() => setFilters(filters.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                </li>
              )
            })}
            {filters.length === 0 && <li className="muted">No filters: every company is ranked.</li>}
          </ul>
          {meta && <AddFilter fields={meta.fields} onAdd={(f) => setFilters([...filters, f])} />}
        </div>

        <div className="rail-block">
          <span className="kicker">Sectors</span>
          <div className="sector-toggles">
            {meta?.sectors.map((s) => (
              <label key={s.name} className="check">
                <input
                  type="checkbox"
                  checked={sectors.length === 0 || sectors.includes(s.name)}
                  onChange={() => toggleSector(s.name)}
                />
                <span>
                  {s.name}
                  <span className="company-name">{s.peer_groups.join(' · ')}</span>
                </span>
              </label>
            ))}
          </div>
        </div>

        {preset && (
          <div className="rail-block">
            <span className="kicker">Ranked on</span>
            <ol className="factor-list">
              {preset.factors.map((f) => (
                <li key={f.field}>
                  {fields.get(f.field)?.label ?? f.field}
                  <span className="muted">{f.higher_is_better ? ', higher is better' : ', lower is better'}</span>
                </li>
              ))}
            </ol>
            <p className="note">
              Each factor is ranked as a percentile within the company's peer group, then
              averaged. A semiconductor is compared with semiconductors, not with pharma.
            </p>
          </div>
        )}
      </aside>

      <section className="span-9">
        <div className="results-head">
          <h1 className="results-title">
            {screen.data ? (
              <>
                <span className="results-count">{screen.data.matched}</span>
                <span className="serif">
                  {' '}
                  of {screen.data.universe} companies pass
                </span>
              </>
            ) : (
              <span className="serif muted">Running the screen…</span>
            )}
          </h1>
          {screen.data && (
            <span className="note num">
              screened in {screen.data.elapsed_ms.toFixed(1)} ms
            </span>
          )}
        </div>
        {screen.error && <p className="error">{(screen.error as Error).message}</p>}
        {screen.data && meta && (
          <div className={screen.isPlaceholderData ? 'is-fetching' : undefined}>
            <ScreenTable rows={screen.data.rows} fields={fields} asOf={asOf} />
          </div>
        )}
      </section>
    </div>
  )
}

function AddFilter({ fields, onAdd }: { fields: FieldInfo[]; onAdd: (f: FilterInfo) => void }) {
  const numeric = fields.filter((f) => ['usd', 'pct', 'multiple', 'price', 'number'].includes(f.format))
  const [field, setField] = useState('ev_ebitda')
  const [op, setOp] = useState('<=')
  const [value, setValue] = useState('')
  const info = numeric.find((f) => f.name === field)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const n = Number(value)
    if (!info || value.trim() === '' || !Number.isFinite(n)) return
    onAdd({ field, op, value: toRaw(n, info.format) })
    setValue('')
  }

  return (
    <form className="add-filter" onSubmit={submit}>
      <select className="select" value={field} onChange={(e) => setField(e.target.value)} aria-label="Field">
        {numeric.map((f) => (
          <option key={f.name} value={f.name}>
            {f.label}
          </option>
        ))}
      </select>
      <select className="select op-select" value={op} onChange={(e) => setOp(e.target.value)} aria-label="Comparison">
        {Object.entries(OP_SYMBOL).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
      <span className="value-input">
        <input
          className="text-input num"
          inputMode="decimal"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="value"
          aria-label="Value"
        />
        <span className="unit">{info ? (UNIT_HINT[info.format] ?? '') : ''}</span>
      </span>
      <button className="button" type="submit">
        Add
      </button>
    </form>
  )
}
