import type { Cell, Format } from './api'

const DASH = '—'

export function isNum(value: Cell | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function usd(value: Cell | undefined, digits = 1): string {
  if (!isNum(value)) return DASH
  const abs = Math.abs(value)
  const sign = value < 0 ? '−' : ''
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(digits)}T`
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(digits)}B`
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`
  return `${sign}$${abs.toFixed(0)}`
}

export function pct(value: Cell | undefined, digits = 1, signed = false): string {
  if (!isNum(value)) return DASH
  const text = `${(Math.abs(value) * 100).toFixed(digits)}%`
  if (value < 0) return `−${text}`
  return signed && value > 0 ? `+${text}` : text
}

export function multiple(value: Cell | undefined, digits = 1): string {
  if (!isNum(value)) return DASH
  return `${value < 0 ? '−' : ''}${Math.abs(value).toFixed(digits)}×`
}

export function price(value: Cell | undefined): string {
  if (!isNum(value)) return DASH
  const text = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return value < 0 ? `−${text}` : text
}

export function number(value: Cell | undefined, digits = 2): string {
  if (!isNum(value)) return DASH
  return value.toFixed(digits)
}

export function byFormat(value: Cell | undefined, format: Format): string {
  switch (format) {
    case 'usd':
      return usd(value)
    case 'pct':
      return pct(value)
    case 'multiple':
      return multiple(value)
    case 'price':
      return price(value)
    case 'number':
      return isNum(value) ? (Number.isInteger(value) ? String(value) : value.toFixed(2)) : DASH
    default:
      return value === null || value === undefined ? DASH : String(value)
  }
}

export const OP_SYMBOL: Record<string, string> = { '>=': '≥', '<=': '≤', '>': '>', '<': '<' }

export function longDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
}

const KEEP_UPPER = new Set(['US', 'USA', 'UK', 'AG', 'NV', 'SA', 'SE', 'LP', 'II', 'III', 'AB', 'ASA'])
const LOWER = new Set(['plc', 'of', 'and', 'the', 'de'])

/** SEC names are often ALL CAPS ("PTC THERAPEUTICS, INC."); render them in title case. */
export function displayName(name: string): string {
  if (name !== name.toUpperCase()) return name
  return name
    .toLowerCase()
    .split(/(\s+|-|\/)/)
    .map((word, i) => {
      const bare = word.replace(/[^a-z]/g, '')
      if (KEEP_UPPER.has(bare.toUpperCase()) && bare.length <= 3 && bare !== 'inc') return word.toUpperCase()
      if (i > 0 && LOWER.has(bare)) return word
      return word.charAt(0).toUpperCase() + word.slice(1)
    })
    .join('')
}
