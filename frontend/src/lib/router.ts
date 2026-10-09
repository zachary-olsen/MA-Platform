// Minimal hash router. All view state (as-of date, screen filters, DCF overrides)
// lives in the URL, so views are shareable and the back button restores them.

import { useSyncExternalStore } from 'react'

function subscribe(callback: () => void) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

export interface Route {
  path: string
  params: URLSearchParams
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#/, '') || '/'
  const [path, query = ''] = raw.split('?')
  return { path: path || '/', params: new URLSearchParams(query) }
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parse(hash)
}

function build(path: string, params: URLSearchParams) {
  const query = params.toString()
  return `#${path}${query ? `?${query}` : ''}`
}

/** Go to a new view (adds a history entry). */
export function navigate(path: string, params = new URLSearchParams()) {
  window.location.hash = build(path, params)
}

/** Change parameters of the current view without adding a history entry. */
export function updateParams(mutate: (params: URLSearchParams) => void) {
  const { path, params } = parse(window.location.hash)
  mutate(params)
  window.history.replaceState(null, '', build(path, params))
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}
