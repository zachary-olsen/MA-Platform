// Typed client for the FastAPI backend (see /docs). Vite proxies /api in development.

export type Format = 'usd' | 'pct' | 'multiple' | 'price' | 'number' | 'text' | 'date'

export interface FieldInfo {
  name: string
  label: string
  format: Format
  description: string
}

export interface FilterInfo {
  field: string
  op: string
  value: number
}

export interface Preset {
  name: string
  description: string
  filters: FilterInfo[]
  factors: { field: string; higher_is_better: boolean }[]
}

export interface Meta {
  latest_price_date: string | null
  company_count: number
  sectors: { name: string; peer_groups: string[] }[]
  presets: Preset[]
  fields: FieldInfo[]
  screen_columns: string[]
}

export type Cell = string | number | null
export type Row = Record<string, Cell>

export interface ScreenResponse {
  as_of: string
  preset: string
  filters: FilterInfo[]
  universe: number
  matched: number
  elapsed_ms: number
  rows: Row[]
}

export interface CompanySummary {
  ticker: string
  name: string
  sector: string | null
  peer_group: string | null
}

export interface CompanyDetail extends CompanySummary {
  cik: number
  industry: string | null
  fiscal_year_end: string | null
  is_active: boolean
  as_of: string
  snapshot: Row | null
  statements: Row[]
  metrics: Row[]
  flags: Record<string, number>
}

export interface MultipleComparison {
  multiple: string
  label: string
  company: number | null
  peer_median: number | null
  vs_peers: number | null
  implied_price: number | null
}

export interface CompsResponse {
  as_of: string
  peer_group: string
  target: Row
  multiples: MultipleComparison[]
  implied_price_median: number | null
  upside: number | null
  peers: Row[]
}

export interface Assumption {
  key: string
  label: string
  value: number
  format: Format
  note: string
  overridden: boolean
}

export interface FootballBar {
  method: string
  low: number
  high: number
  mid: number | null
  kind: 'market' | 'comps' | 'dcf' | 'monte_carlo'
  detail: string | null
}

export interface Valuation {
  as_of: string
  ticker: string
  fiscal_year: number
  assumptions: Assumption[]
  projection: Row[]
  bridge: {
    pv_explicit: number
    terminal_value: number
    pv_terminal: number
    terminal_share: number
    enterprise_value: number
    net_debt: number
    other_claims: number
    equity_value: number
    shares: number
    value_per_share: number
    price: number
    upside: number
  }
  sensitivity: { waccs: number[]; growths: number[]; values: (number | null)[][] }
  monte_carlo: {
    draws: number
    discarded: number
    percentiles: Record<string, number>
    prob_above_price: number
    growth_volatility: number
    margin_volatility: number
    histogram: { x0: number; x1: number; count: number }[]
  } | null
  football_field: { price: number; bars: FootballBar[] }
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function get<T>(path: string, params?: URLSearchParams): Promise<T> {
  const query = params && params.toString() ? `?${params}` : ''
  const response = await fetch(`/api${path}${query}`)
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // keep statusText
    }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

function withAsOf(asOf: string | null, extra?: Record<string, string | string[] | undefined>) {
  const params = new URLSearchParams()
  if (asOf) params.set('as_of', asOf)
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (value === undefined) continue
    for (const v of Array.isArray(value) ? value : [value]) params.append(key, v)
  }
  return params
}

export const api = {
  meta: () => get<Meta>('/meta'),
  screen: (asOf: string | null, preset: string, filters: string[], sectors: string[]) =>
    get<ScreenResponse>(
      '/screen',
      withAsOf(asOf, { preset, filter: filters, replace_filters: 'true', sector: sectors }),
    ),
  search: (q: string) => get<CompanySummary[]>('/companies', new URLSearchParams({ q, limit: '8' })),
  company: (ticker: string, asOf: string | null) =>
    get<CompanyDetail>(`/companies/${ticker}`, withAsOf(asOf)),
  comps: (ticker: string, asOf: string | null) =>
    get<CompsResponse>(`/companies/${ticker}/comps`, withAsOf(asOf)),
  valuation: (ticker: string, asOf: string | null, overrides: Record<string, string | undefined>) =>
    get<Valuation>(`/companies/${ticker}/valuation`, withAsOf(asOf, overrides)),
}
