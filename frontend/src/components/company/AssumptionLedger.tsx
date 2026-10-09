import { useState } from 'react'
import type { Assumption } from '../../lib/api'
import { number, pct } from '../../lib/format'

/** Assumption key -> URL override parameter. Margin start/end share one override. */
const OVERRIDE_PARAM: Record<string, string> = {
  growth_start: 'g',
  margin_start: 'm',
  margin_end: 'm',
  wacc: 'w',
  terminal_growth: 'tg',
}

function display(a: Assumption) {
  return a.format === 'pct' ? pct(a.value, a.key === 'terminal_growth' ? 2 : 1) : number(a.value)
}

export function AssumptionLedger({
  assumptions,
  onOverride,
  onReset,
}: {
  assumptions: Assumption[]
  onOverride: (param: string, value: number | null) => void
  onReset: () => void
}) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const anyOverride = assumptions.some((a) => a.overridden)

  const commit = (a: Assumption) => {
    const n = Number(draft)
    if (draft.trim() !== '' && Number.isFinite(n)) onOverride(OVERRIDE_PARAM[a.key], n / 100)
    setEditing(null)
  }

  return (
    <div>
      <div className="ledger-head">
        <span className="kicker">Assumptions</span>
        {anyOverride ? (
          <button className="link-button" onClick={onReset}>
            Reset all to defaults
          </button>
        ) : (
          <span className="note">Click a dotted value to change it</span>
        )}
      </div>
      <dl className="ledger">
        {assumptions.map((a) => {
          const param = OVERRIDE_PARAM[a.key]
          const isEditing = editing === a.key
          return (
            <div key={a.key} className={a.overridden ? 'ledger-row overridden' : 'ledger-row'}>
              <dt>
                {a.label}
                <span className="ledger-note">{a.note}</span>
              </dt>
              <dd className="num">
                {isEditing ? (
                  <span className="ledger-input">
                    <input
                      autoFocus
                      className="text-input num"
                      inputMode="decimal"
                      value={draft}
                      aria-label={`${a.label}, percent`}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commit(a)
                        if (e.key === 'Escape') setEditing(null)
                      }}
                      onBlur={() => commit(a)}
                    />
                    %
                  </span>
                ) : param ? (
                  <button
                    className="editable"
                    title="Edit"
                    onClick={() => {
                      setDraft((a.value * 100).toFixed(a.key === 'terminal_growth' ? 2 : 1))
                      setEditing(a.key)
                    }}
                  >
                    {display(a)}
                  </button>
                ) : (
                  display(a)
                )}
                {a.overridden && param && (
                  <button className="link-button undo" onClick={() => onOverride(param, null)} aria-label={`Reset ${a.label}`}>
                    reset
                  </button>
                )}
              </dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
