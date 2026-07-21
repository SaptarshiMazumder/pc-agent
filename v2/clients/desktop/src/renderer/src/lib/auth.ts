/**
 * Web sign-in state — the browser half of platform accounts (M1b).
 *
 * "Accounts mode" is ON when an accounts-service URL is configured (VITE_AGENTD_ACCOUNTS_URL,
 * or ?accounts=<url> for local testing). In that mode the app shows a sign-in gate and connects
 * the daemon with the signed-in account's SESSION TOKEN (never a machine token). Off (the desktop
 * app, and any web build without an accounts URL) => this module is inert and the app connects as
 * it did before.
 *
 * The session token is the browser's only credential; the model key stays server-side. We keep the
 * session in localStorage and expose a tiny external store so React re-renders on sign-in/out.
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

/** The accounts-service base URL (no trailing slash), or '' when accounts mode is off. */
export function accountsUrl(): string {
  const q = new URLSearchParams(typeof location !== 'undefined' ? location.search : '')
  const env = (import.meta as { env?: Record<string, string> }).env || {}
  const raw = q.get('accounts') || env.VITE_AGENTD_ACCOUNTS_URL || ''
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
