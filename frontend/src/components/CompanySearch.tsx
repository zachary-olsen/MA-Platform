import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { api } from '../lib/api'
import { displayName } from '../lib/format'
import { navigate } from '../lib/router'

export function CompanySearch({ asOf }: { asOf: string | null }) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query.trim()), 150)
    return () => window.clearTimeout(t)
  }, [query])

  const results = useQuery({
    queryKey: ['search', debounced],
    queryFn: () => api.search(debounced),
    enabled: debounced.length > 0,
  })
  const items = debounced ? (results.data ?? []) : []

  const go = (ticker: string) => {
    const params = new URLSearchParams()
    if (asOf) params.set('as_of', asOf)
    navigate(`/c/${ticker}`, params)
    setQuery('')
    setOpen(false)
  }

  return (
    <div className="search">
      <label className="field-label" htmlFor={`${listId}-input`}>
        Company
      </label>
      <input
        id={`${listId}-input`}
        className="text-input"
        placeholder="Ticker or name — e.g. DXCM, Lam"
        value={query}
        role="combobox"
        aria-expanded={open && items.length > 0}
        aria-controls={listId}
        autoComplete="off"
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((i) => Math.min(i + 1, items.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((i) => Math.max(i - 1, 0))
          } else if (e.key === 'Enter' && items[active]) {
            go(items[active].ticker)
          } else if (e.key === 'Escape') {
            setOpen(false)
          }
        }}
      />
      {open && items.length > 0 && (
        <ul className="search-results" id={listId} role="listbox">
          {items.map((item, i) => (
            <li
              key={item.ticker}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                go(item.ticker)
              }}
            >
              <span className="ticker">{item.ticker}</span>
              <span>
                {displayName(item.name)}
                <span className="company-name">{item.peer_group}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
