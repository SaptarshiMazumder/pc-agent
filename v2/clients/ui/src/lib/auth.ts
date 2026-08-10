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

import { gateway } from '../gateway/client'
import { isDesktop, randomUuid } from './host'

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

/**
 * Sign in THROUGH THE DAEMON (desktop), so it is the daemon that knows.
 *
 * This window used to POST /login itself and keep the result to itself. The daemon therefore had
 * no idea anyone had signed in — so it could not tell the other windows, and Agent Builder went
 * on showing "Not signed in" after you signed in here. Sign-OUT already went through the daemon,
 * which is why that direction propagated and this one did not.
 *
 * The token is then read back with `auth.token`, a HOST-ONLY method: an agent window is refused
 * it by the scope gate. This client still needs the value because it calls the accounts service
 * directly for /resolve, /me/credits and /me/purchase. When those move behind the daemon, this
 * second call goes away.
 */
async function daemonLogin(email: string, password: string, signup: boolean): Promise<Session> {
  await gateway.request('auth.login', { email, password, signup })
  const d = await gateway.request<{ token: string; email: string; accountId: string }>('auth.token')
  const s: Session = { token: d.token, accountId: d.accountId, email: d.email || email }
  setSession(s)
  return s
}

export async function login(email: string, password: string): Promise<Session> {
  const clean = email.trim().toLowerCase()
  if (isDesktop) return daemonLogin(clean, password, false)
  // WEB: the session token IS this client's socket credential, so there is no daemon connection
  // to sign in through until we already have one. The direct call is not a shortcut here, it is
  // the only order that works.
  const d = await post('/login', { email: clean, password })
  const s: Session = { token: d.token, accountId: d.account_id, email: d.email }
  setSession(s)
  return s
}

/**
 * Adopt a sign-in that happened SOMEWHERE ELSE — another agent's window, or a second client.
 *
 * The daemon is the one that knows who is signed in, and it announces changes with
 * `auth.changed`. Sign-out propagated from the start, because forgetting needs no credential.
 * Signing in did not: this client's Session carries the token itself (it calls the accounts
 * service directly for /resolve, /me/credits and /me/purchase), and there was no way to obtain
 * one it had not minted. So signing in inside Agent Builder left agentd still showing signed out.
 *
 * `auth.token` closes that. It is host-only, so this shell can read the daemon's stored token
 * while an agent's page is refused it by the scope gate.
 *
 * Returns null when there is nothing to adopt — no daemon support, no session, or this client
 * already holds the same one.
 */
export async function adoptDaemonSession(): Promise<Session | null> {
  if (!isDesktop) return null
  try {
    const d = await gateway.request<{ token: string; email: string; accountId: string }>('auth.token')
    if (!d?.token) return null
    const current = getSession()
    if (current && current.token === d.token) return null
    const s: Session = { token: d.token, accountId: d.accountId, email: d.email }
    setSession(s)
    return s
  } catch {
    return null // older daemon, or a connection that may not ask
  }
}

export async function signup(email: string, password: string): Promise<Session> {
  const clean = email.trim().toLowerCase()
  if (isDesktop) return daemonLogin(clean, password, true)
  await post('/signup', { email: clean, password })
  return login(clean, password)
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

/** What the signed-in account can spend right now. */
export type Credits = {
  creditsRemaining: number
  fundingSource: string
  creditClass: string
  modelTierMax: string
  entitlementRequired: boolean
  entitled: boolean
  expiresAt: number
}

/**
 * Read the account's own balance from the accounts service.
 *
 * Called with the SESSION TOKEN and no account id — the service resolves the account from the
 * token, so this can only ever return the caller's own balance. Returns null rather than
 * throwing: a balance is decoration, and a metering hiccup must not break the chat that is
 * already running.
 */
export async function fetchCredits(agentId = ''): Promise<Credits | null> {
  const s = getSession()
  if (!s || !isAccountsMode()) return null
  try {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
    const r = await fetch(accountsUrl() + '/me/credits' + q, {
      headers: { Authorization: `Bearer ${s.token}` }
    })
    if (!r.ok) return null
    const d = (await r.json()) as Record<string, unknown>
    return {
      creditsRemaining: Number(d.credits_remaining || 0),
      fundingSource: String(d.funding_source || ''),
      creditClass: String(d.credit_class || ''),
      modelTierMax: String(d.model_tier_max || ''),
      entitlementRequired: Boolean(d.entitlement_required),
      entitled: d.entitled !== false,
      expiresAt: Number(d.expires_at || 0)
    }
  } catch {
    return null
  }
}

// --------------------------------------------------------------------------- buying credits

/** One thing on the shelf. Shaped by the server's `products` row, never by the client. */
export type CreditPack = {
  id: string
  kind: string
  title: string
  priceUsd: number
  credits: number
  modelTierMax: string
  periodDays: number
}

export type Catalog = {
  packs: CreditPack[]
  /** Which payment rail is configured. For display only — never branch behaviour on it. */
  provider: string
  /** The rail's own sentence about what confirming will do ("no card is charged", or later the
   *  real thing). Rendered verbatim so swapping the rail rewrites the disclosure itself. */
  paymentNote: string
}

function toPack(d: Record<string, unknown>): CreditPack {
  return {
    id: String(d.id || ''),
    kind: String(d.kind || ''),
    title: String(d.title || ''),
    priceUsd: Number(d.price_usd || 0),
    credits: Number(d.credits || 0),
    modelTierMax: String(d.model_tier_max || ''),
    periodDays: Number(d.period_days || 0)
  }
}

/**
 * What is for sale. PUBLIC — no token, because a store has to be browsable before you sign in.
 *
 * The shelf is asked for by `kind`, so a new kind of product (an agent subscription, a seat)
 * does not silently appear in the buy-credits dialog.
 */
export async function fetchCatalog(kind = 'credit_pack'): Promise<Catalog | null> {
  if (!isAccountsMode()) return null
  try {
    const r = await fetch(`${accountsUrl()}/products?kind=${encodeURIComponent(kind)}`)
    if (!r.ok) return null
    const d = (await r.json()) as { products?: Record<string, unknown>[]; provider?: string; payment_note?: string }
    return {
      packs: (d.products || []).map(toPack),
      provider: String(d.provider || ''),
      paymentNote: String(d.payment_note || '')
    }
  } catch {
    return null
  }
}

export type Purchase = {
  ok: boolean
  replayed: boolean
  credits: number
  priceUsd: number
  creditsRemaining: number
  /** The rail's own account of what it did — shown as-is on the receipt line. */
  paymentDetail: string
}

/**
 * Buy one thing from the catalogue as the signed-in account.
 *
 * SENDS ONLY A product_id. Price and credit count come from the server's row — a client that
 * could name its own price could mint itself a fortune, so there is deliberately no parameter
 * for either here.
 *
 * The idempotency key is minted PER CALL (one per button press), so a retry after a lost
 * response returns the original purchase instead of buying a second pack. Unlike the other
 * helpers this THROWS on failure: a silent null is right for a balance you are decorating a
 * screen with, and wrong for a purchase the user is waiting on.
 */
export async function purchase(productId: string): Promise<Purchase> {
  const s = getSession()
  if (!s || !isAccountsMode()) throw new Error('sign in to buy credits')
  const r = await fetch(`${accountsUrl()}/me/purchase`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${s.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ product_id: productId, idempotency_key: newIdempotencyKey() })
  })
  const d = (await r.json().catch(() => ({}))) as Record<string, unknown>
  if (!r.ok) throw new Error(String(d.detail || `purchase failed (HTTP ${r.status})`))
  notifyCreditsChanged()
  const payment = (d.payment || {}) as Record<string, unknown>
  return {
    ok: true,
    replayed: d.replayed === true,
    credits: Number(d.credits || 0),
    priceUsd: Number(d.price_usd || 0),
    creditsRemaining: Number(d.credits_remaining || 0),
    paymentDetail: String(payment.detail || '')
  }
}

/** ONE fallback rule for the whole renderer — see `randomUuid` in lib/host.ts for why
 *  crypto.randomUUID cannot be called directly. This used to carry its own weaker fallback;
 *  two answers to the same host limitation is how one of them stays broken. */
function newIdempotencyKey(): string {
  return randomUuid()
}

// A balance can change without this tab doing anything that re-renders it: a purchase on the
// Credits page must move the chip in the composer. One tiny bus, rather than every consumer
// polling — polling a money endpoint on a timer is a cost we would pay forever.
const creditListeners = new Set<() => void>()

/** Subscribe to "the balance probably changed"; returns an unsubscribe. */
export function onCreditsChanged(cb: () => void): () => void {
  creditListeners.add(cb)
  return () => creditListeners.delete(cb)
}

/** Announce a balance change. Called by `purchase`; safe to call after any known debit. */
export function notifyCreditsChanged(): void {
  creditListeners.forEach((l) => l())
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
