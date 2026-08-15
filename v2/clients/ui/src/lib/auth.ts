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
import { platformDoc } from './discovery'
import { hostOs, isDesktop, randomUuid } from './host'
import {
  clearTokens,
  configureTokens,
  currentPair,
  getAccessToken,
  pairFromResponse,
  restore as restoreTokens,
  setPair
} from './tokens'

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

/**
 * The accounts-service base URL (no trailing slash), or '' when accounts mode is off.
 *
 * PRECEDENCE, and why discovery sits where it does: an explicit `?accounts=` or a build-time env
 * is somebody deliberately overriding, so those still win. Below them comes what the DEPLOYMENT
 * says today (`/.well-known/agentd-platform`, resolved from the single baked `platform_url`),
 * and only then the per-service URL an installer froze months ago.
 *
 * That ordering is the fix for the bug where the same email was two different accounts: baked
 * ALB hostnames rot on every destroy/recreate, so two clients built at different times signed in
 * against two different databases. One baked address plus a fetch removes the whole class.
 */
export function accountsUrl(): string {
  const q = new URLSearchParams(typeof location !== 'undefined' ? location.search : '')
  const env = (import.meta as { env?: Record<string, string> }).env || {}
  const raw =
    q.get('accounts') ||
    env.VITE_AGENTD_ACCOUNTS_URL ||
    platformDoc()?.authUrl ||
    configured
  return (raw || '').replace(/\/$/, '')
}

/**
 * Sign-in providers to offer, from the deployment rather than from this file.
 *
 * The UI renders buttons from THIS list, so adding Google or Microsoft later is a row in the
 * discovery document plus a server-side adapter — no client release. Same rule the rest of the
 * codebase follows for models, tools and plugins: capabilities are data, never a hardcoded list.
 *
 * Defaults to the password form, so a deployment that predates discovery behaves as it always has.
 */
export function authProviders(): Array<{ id: string; label: string; kind: string }> {
  const doc = platformDoc()
  if (!doc || !doc.providers.length) return [{ id: 'local', label: 'Email', kind: 'password' }]
  return doc.providers
}

export function isAccountsMode(): boolean {
  return !!accountsUrl()
}

// Wired ONCE, at module load, and as a RESOLVER rather than a value. Every path that mints or
// spends a token — sign-in, restore, the renewal timer, sign-out — needs the accounts address,
// and having each of them remember to configure it first is how one of them does not. (One did:
// signing in fresh skipped it, so the renewal timer had nowhere to send its request and every
// refresh silently returned null.) Reading it lazily also means discovery resolving after this
// module loads is picked up with no re-configuration.
configureTokens(() => accountsUrl())

export function getSession(): Session | null {
  return cached
}

export function signOut(): void {
  setSession(null)
  // Revoke server-side too, not just locally. Forgetting a 30-day refresh token without telling
  // the server leaves a live credential on a machine the user may have just stopped trusting.
  void clearTokens()
  // Same reason as sign-in: the credential lives in the socket url, so the daemon keeps treating
  // this client as the old account until the socket is rebuilt without it.
  gateway.reconnect()
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
 * Sign in. ONE path for desktop and web, because the daemon no longer has a second one.
 *
 * There used to be a desktop-only route through the daemon (`auth.login` + `auth.token`) so that
 * IT held the session and could tell other windows. Those methods do not exist: identity is now
 * a property of each CONNECTION — presented as `?session=` when the socket opens — and the daemon
 * stores nothing. Calling them errored, which is why signing in on desktop failed outright.
 *
 * So this client does what the web client always did: POST to the accounts service, keep the
 * session, and REBUILD THE SOCKET. The reconnect is not a refresh; it is how the credential
 * reaches the daemon at all.
 *
 * Known limit, stated because the old code existed to solve it: a sign-in here does not
 * propagate to an agent's own window. Each window presents its own session on its own socket,
 * and they do not share storage. A daemon-side broadcast is the fix, and it needs a daemon-side
 * identity to broadcast — which this design deliberately does not have.
 */
export async function login(email: string, password: string): Promise<Session> {
  const clean = email.trim().toLowerCase()
  // ONE credential kind. This returns a PAIR: a short-lived access token that travels on every
  // request, and a refresh token that never leaves this device except to be exchanged. The
  // opaque `sess_` session this used to fall back to no longer exists anywhere.
  const d = await post('/auth/login', {
    email: clean,
    password,
    client_id: isDesktop ? 'desktop' : 'web',
    device_label: deviceLabel()
  })
  const p = pairFromResponse(d as unknown as Record<string, unknown>)
  await setPair(p)
  const s: Session = { token: p.accessToken, accountId: p.accountId, email: p.email || clean }
  setSession(s)
  gateway.reconnect()
  return s
}

/** A human-readable name for the "your devices" list. Best-effort; never blocks sign-in. */
function deviceLabel(): string {
  try {
    return `${isDesktop ? 'Desktop' : 'Web'} · ${hostOs() || 'unknown'}`
  } catch {
    return isDesktop ? 'Desktop' : 'Web'
  }
}

export async function signup(email: string, password: string): Promise<Session> {
  const clean = email.trim().toLowerCase()
  await post('/signup', { email: clean, password })
  return login(clean, password)
}

/**
 * Re-establish a session from the stored refresh token, at app start.
 *
 * This is what makes "stay signed in" work with a ten-minute access token: nothing durable is
 * kept except the refresh token, and one exchange at boot turns it into a usable pair. Returns
 * null when there is nothing stored or the session is genuinely over.
 */
export async function restoreSession(): Promise<Session | null> {
  if (!isAccountsMode()) return null
  const p = await restoreTokens()
  if (!p) return null
  const s: Session = { token: p.accessToken, accountId: p.accountId, email: p.email }
  setSession(s)
  return s
}

/** The freshest access token, refreshing if needed. Used to build the socket URL. */
export async function currentAccessToken(): Promise<string> {
  if (!isAccountsMode()) return ''
  const live = await getAccessToken()
  if (live) {
    // Keep the rendered session in step with the token that is actually in use, so the UI never
    // shows "signed in" against a credential that has been replaced.
    const p = currentPair()
    if (p && cached && p.accessToken !== cached.token) {
      setSession({ token: p.accessToken, accountId: p.accountId, email: p.email })
    }
    return live
  }
  return cached?.token || ''
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
