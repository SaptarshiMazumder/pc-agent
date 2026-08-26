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

import { BillingClient } from '@agentd/billing'
import type { Catalog, CreditPack, Credits, Purchase } from '@agentd/billing'

import { gateway } from '../gateway/client'
import { platformDoc } from './discovery'
import { hostBroadcastAppToken, randomUuid } from './host'
import {
  clearTokens,
  configureTokens,
  currentPair,
  getAccessToken,
  onTokens,
  restore as restoreTokens,
  tokens
} from './tokens'

export interface Session {
  token: string
  accountId: string
  email: string
}

const listeners = new Set<() => void>()

/**
 * The rendered session, DERIVED from the credential rather than stored beside it.
 *
 * This module used to keep its own `agentd.session` row in localStorage, written by hand at each
 * of the four places that changed a token. A renewal replaces the access token without going
 * through any of them, so the screen could show one credential while the socket presented
 * another — and a stale row survived a sign-out that failed halfway. There is one source of truth
 * now; this is a projection of it, kept as a cached snapshot only because `useSyncExternalStore`
 * needs a stable reference between changes.
 */
let cached: Session | null = null

function project(): Session | null {
  const p = currentPair()
  return p && p.accessToken
    ? { token: p.accessToken, accountId: p.accountId, email: p.email }
    : null
}

function announce(): void {
  cached = project()
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
// Every change to the credential — sign-in, renewal, sign-out — re-renders whoever is showing it.
// Subscribing HERE rather than in each caller is what makes the projection above trustworthy.
onTokens(announce)
// THE PUSH CHAIN'S SEND SIDE. This shell holds the only refresh token; agent app windows run on
// ten-minute access tokens they cannot renew. Main and both preloads have carried the pipe for a
// while (app:broadcastToken -> agentdHost.onAccessToken) — but nothing ever put a token INTO it,
// so every app window went anonymous at its first expiry. Every rotation goes down the pipe now;
// each window's manager decides for itself whether to adopt what arrives.
onTokens((p) => hostBroadcastAppToken(p?.accessToken || ''))
cached = project()

export function getSession(): Session | null {
  return cached
}

export function signOut(): void {
  // Revokes server-side too, not just locally. Forgetting a 30-day refresh token without telling
  // the server leaves a live credential on a machine the user may have just stopped trusting.
  void clearTokens()
  // Same reason as sign-in: the credential lives in the socket url, so the daemon keeps treating
  // this client as the old account until the socket is rebuilt without it.
  gateway.reconnect()
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
  return enter({ email, password })
}

export async function signup(email: string, password: string): Promise<Session> {
  return enter({ email, password, signup: true })
}

/**
 * ONE credential kind, from the ONE implementation.
 *
 * The exchange itself — which endpoint, which fields, what to do with the pair that comes back —
 * belongs to `@agentd/auth`, so that this client and every agent window ask the same server the
 * same question. Signing up is the same call with a flag: it is the same credential at the end,
 * and having a second path here is how the two drifted the first time.
 */
async function enter(args: {
  email: string
  password: string
  signup?: boolean
}): Promise<Session> {
  const p = await tokens().login(args)
  const s: Session = {
    token: p.accessToken,
    accountId: p.accountId,
    email: p.email || args.email.trim().toLowerCase()
  }
  // The credential lives in the socket url, so the daemon goes on treating this client as whoever
  // it was until the socket is rebuilt with the new one.
  gateway.reconnect()
  return s
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
  await restoreTokens()
  return getSession()
}

/**
 * The freshest access token, refreshing if needed. Used to build the socket URL.
 *
 * Nothing re-renders here any more: the manager announces every change, so the screen follows the
 * credential without this function having to remember to say so.
 */
export async function currentAccessToken(): Promise<string> {
  if (!isAccountsMode()) return ''
  return (await getAccessToken()) || cached?.token || ''
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
/**
 * Read the account's own balance from the accounts service.
 *
 * Called with the SESSION TOKEN and no account id — the service resolves the account from the
 * token, so this can only ever return the caller's own balance. Returns null rather than
 * throwing: a balance is decoration, and a metering hiccup must not break the chat that is
 * already running.
 */
/**
 * MONEY LIVES IN `@agentd/billing`, NOT HERE.
 *
 * These four used to be four fetches in this file. They are now four one-liners over the shared
 * client, for the same reason sign-in moved to `@agentd/auth`: an agent window shows the same
 * balance and buys from the same shelf, and two implementations of "what a purchase is" is two
 * sets of idempotency and refusal bugs. The signatures are unchanged, so every component that
 * calls them is untouched.
 *
 * The three facts this client answers differently from an agent window — where accounts is, what
 * the current token is, and how to mint an idempotency key — are exactly what `BillingHost` asks
 * for, and are all this file still owns about money.
 */
const shop = new BillingClient({
  accountsUrl,
  accessToken: currentAccessToken,
  // ONE fallback rule for the whole renderer — see `randomUuid` in lib/host.ts for why
  // crypto.randomUUID cannot be called directly.
  newKey: randomUuid
})

export async function fetchCredits(agentId = ''): Promise<Credits | null> {
  if (!getSession() || !isAccountsMode()) return null
  return shop.credits(agentId)
}

export async function fetchCatalog(kind = 'credit_pack'): Promise<Catalog | null> {
  if (!isAccountsMode()) return null
  return shop.catalog(kind)
}

/**
 * Buy a pack.
 *
 * Goes through `/me/checkout`, which is a strict superset of the old `/me/purchase`: on the rail
 * configured today it settles in place and returns the completed purchase, and on a card rail it
 * returns a link to go and pay. `checkoutUrl` is empty in the first case, which is the only thing
 * a caller has to look at — never at which rail is configured.
 */
export async function purchase(productId: string, orgId = ''): Promise<Purchase> {
  if (!getSession() || !isAccountsMode()) throw new Error('sign in to buy credits')
  // Our own address goes along ONLY when it is a web URL. In the installed shell the page loads
  // from disk (file://...), and telling a checkout "return the customer to a file on my C: drive"
  // is a value the accounts service rightly refuses — so there, the field stays empty, which the
  // current settle-in-place rail ignores anyway. A card rail on desktop will need a deep link.
  const page = typeof location === 'undefined' ? '' : location.href.split('#')[0]
  return shop.buy(productId, /^https?:\/\//.test(page) ? page : '', orgId)
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

// The money types and the "balance probably changed" bus come from `@agentd/billing`, which every
// agent window also uses. Re-exported from here so components keep one import site, while there is
// still exactly ONE definition of each in the product.
export type { Catalog, CreditPack, Credits, Purchase } from '@agentd/billing'
export { notifyCreditsChanged, onCreditsChanged } from '@agentd/billing'
