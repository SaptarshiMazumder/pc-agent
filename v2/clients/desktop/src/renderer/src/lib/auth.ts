/**
 * Sign-in state — the client half of platform accounts (M1b).
 *
 * "Accounts mode" is ON when an accounts-service URL is configured: ?accounts=<url> (local
 * testing) > VITE_AGENTD_ACCOUNTS_URL (web builds) > configureAccounts() (DESKTOP hosted
 * flavors — main.tsx feeds it the flavor's [platform] accounts_url before first render). In
 * that mode the app shows a sign-in gate. On the WEB the daemon connection presents the
 * session token; on DESKTOP the local daemon keeps its machine token and the session token
 * becomes the MODEL-GATEWAY credential instead (store.ts calls platform.connect after each
 * handshake). No URL from any source => this module is inert and everything works as before.
 *
 * The session token is the client's only credential; the model key stays server-side. We keep
 * the session in localStorage and expose a tiny external store so React re-renders on
 * sign-in/out.
 */

import { useSyncExternalStore } from 'react'

export interface Session {
  token: string
  accountId: string
  email: string
}

const LS_KEY = 'agentd.session'

const listeners = new Set<() => void>()
function readLS(): Session | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    return null
  }
}
// cached snapshot so useSyncExternalStore sees a STABLE reference between changes
let cached: Session | null = readLS()

function setSession(s: Session | null): void {
  cached = s
  try {
    if (s) localStorage.setItem(LS_KEY, JSON.stringify(s))
    else localStorage.removeItem(LS_KEY)
  } catch {
    /* private mode / quota — the in-memory cache still drives this session */
  }
  listeners.forEach((l) => l())
}

// Runtime-configured accounts URL (desktop hosted flavors; set from the flavor before render).
let configured = ''

/** Point accounts mode at a service at RUNTIME — the desktop path, where the URL comes from
 *  the build's distribution.toml rather than a query param or a build-time env. */
export function configureAccounts(url: string): void {
  configured = (url || '').replace(/\/$/, '')
}

/** The accounts-service base URL (no trailing slash), or '' when accounts mode is off. */
export function accountsUrl(): string {
  const q = new URLSearchParams(typeof location !== 'undefined' ? location.search : '')
  const env = (import.meta as { env?: Record<string, string> }).env || {}
  const raw = q.get('accounts') || env.VITE_AGENTD_ACCOUNTS_URL || configured
  return raw.replace(/\/$/, '')
}

export function isAccountsMode(): boolean {
  return !!accountsUrl()
}

export function getSession(): Session | null {
  return cached
}

export function signOut(): void {
  setSession(null)
}

async function post(path: string, body: unknown): Promise<Record<string, string>> {
  const r = await fetch(accountsUrl() + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  const data = (await r.json().catch(() => ({}))) as Record<string, string>
  if (!r.ok) throw new Error(data.detail || `sign-in failed (HTTP ${r.status})`)
  return data
}

export async function login(email: string, password: string): Promise<Session> {
  const d = await post('/login', { email: email.trim().toLowerCase(), password })
  const s: Session = { token: d.token, accountId: d.account_id, email: d.email }
  setSession(s)
  return s
}

export async function signup(email: string, password: string): Promise<Session> {
  await post('/signup', { email: email.trim().toLowerCase(), password })
  return login(email, password)
}

/**
 * Re-check the stored session against the accounts service. Returns:
 *   'valid'   — token still resolves
 *   'invalid' — the service DEFINITIVELY rejected it (401/403) => sign the user out
 *   'unknown' — network/service trouble; do NOT sign out on a flaky connection
 */
export async function resolveSession(): Promise<'valid' | 'invalid' | 'unknown'> {
  const s = getSession()
  if (!s || !isAccountsMode()) return 'invalid'
  try {
    const r = await fetch(accountsUrl() + '/resolve', {
      headers: { Authorization: `Bearer ${s.token}` }
    })
    if (r.ok) return 'valid'
    return r.status === 401 || r.status === 403 ? 'invalid' : 'unknown'
  } catch {
    return 'unknown'
  }
}

/** React hook: the current session (re-renders on sign-in/out). */
export function useAuthSession(): Session | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    getSession,
    getSession
  )
}
