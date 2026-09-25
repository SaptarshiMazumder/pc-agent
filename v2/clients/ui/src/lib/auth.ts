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

import { accessTokenExpiry } from '@agentd/auth'
import { BillingClient } from '@agentd/billing'
import type { Catalog, CreditPack, Credits, Purchase } from '@agentd/billing'

import { gateway } from '../gateway/client'
import { platformDoc } from './discovery'
import { hostAuthRequest, hostOAuthSignIn, hostSecrets, isDesktop, randomUuid } from './host'
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

/** DESKTOP: the machine's session as the runtime last answered it. The runtime is the only
 *  holder (agent_runtime/infrastructure/platform_session.py); this is a mirror for rendering. */
let machine: Session | null = null

function project(): Session | null {
  if (isDesktop) return machine
  const p = currentPair()
  return p && p.accessToken
    ? { token: p.accessToken, accountId: p.accountId, email: p.email }
    : null
}

/** One /auth/* ask through the shell's main process. Status 0 = the daemon itself is away. */
async function askRuntime(
  path: string,
  headers?: Record<string, string>
): Promise<{ status: number; body: Record<string, unknown> }> {
  const call = hostAuthRequest(path, headers)
  if (!call) return { status: 0, body: {} }
  return call
}

/** Re-read the machine's sign-in state from the runtime and re-render whoever shows it. */
async function refreshMachine(): Promise<void> {
  const r = await askRuntime('/auth/token')
  const b = r.body as { state?: string; accessToken?: string; accountId?: string; email?: string }
  machine =
    b?.state === 'ok' && b.accessToken
      ? {
          token: String(b.accessToken),
          accountId: String(b.accountId || ''),
          email: String(b.email || '')
        }
      : r.status === 0 || b?.state === 'accounts_unreachable'
        ? machine // the network, not the credential — keep rendering the session we know
        : null
  announce()
}

/**
 * ONE-TIME MIGRATION from the per-window world: the refresh token this desktop stored before
 * the runtime became the holder (OS-encrypted via the preload bridge, or localStorage on
 * installs older than that). Handed to the runtime, which validates it by USING it — one
 * refresh rotates it into platform-session.json — and then cleared here so no second holder
 * survives. A DEAD legacy token is cleared too (retrying it every boot buys nothing); an
 * unreachable service keeps it for the next boot.
 */
async function migrateLegacySession(): Promise<void> {
  const secrets = hostSecrets()
  let legacy = ''
  try {
    legacy = (await secrets?.read()) || ''
  } catch {
    legacy = ''
  }
  if (!legacy) {
    try {
      legacy = localStorage.getItem('agentd.refresh') || ''
    } catch {
      legacy = ''
    }
  }
  if (!legacy) return
  const r = await askRuntime('/auth/adopt', { 'X-Auth-Refresh': legacy })
  const state = String((r.body as { state?: string })?.state || '')
  if (state === 'ok' || state === 'session_expired' || state === 'signed_out') {
    try {
      await secrets?.write(null)
    } catch {
      /* the bridge failing to clear is survivable — adopt already rotated the token */
    }
    try {
      localStorage.removeItem('agentd.refresh')
      localStorage.removeItem('agentd.auth')
    } catch {
      /* nothing stored */
    }
  }
  if (state === 'ok') await refreshMachine()
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

/* ── external sign-in ("Continue with Google") ──────────────────────────────────────────
 *
 * THE SERVER OWNS THE FLOW. `/auth/authorize` hands back a URL to visit and the `state` that
 * identifies the attempt; `/auth/callback` redeems the code it comes back with. Nothing here
 * knows which provider is which — the buttons come from `authProviders()` above, which comes
 * from the deployment's discovery document.
 *
 * TWO SHAPES, BECAUSE A PACKAGED APP CANNOT BE A REDIRECT TARGET. Google will not send a browser
 * to a file:// page, so the desktop cannot do what the web does here.
 *
 *   web      this page navigates away, the provider returns to it with `?code=&state=`, and the
 *            flow is finished by `finishExternalSignIn` on the next load. The refresh half goes
 *            into an HttpOnly cookie, so no script on the page can read it.
 *   desktop  the HOST runs it (desktop/src/main/oauthLoopback.ts): the system browser, where the
 *            user's saved passwords already are, and a listener on 127.0.0.1 to catch the code
 *            (RFC 8252). The session is adopted by the daemon and never reaches this renderer.
 *
 * WHAT IS COMMON is everything that matters: the same `/auth/authorize` and `/auth/callback` on
 * the same accounts service, the same PKCE and `state` checks, the same provider list out of the
 * discovery document. The two differ only in who holds the browser and who ends up holding the
 * session — which is exactly how the password path already differs between them. */

const OAUTH_FLOW_KEY = 'agentd.oauth.flow'

interface PendingOAuth {
  provider: string
  state: string
  verifier: string
}

/** Where the provider sends the browser back: this page, with no query and no hash.
 *
 *  EVERY DISTINCT VALUE HERE MUST BE REGISTERED with the provider, so it is computed from the
 *  address the app is actually served at rather than configured in a second place that can drift
 *  from the first. */
export function oauthRedirectUri(): string {
  const u = new URL(location.href)
  u.search = ''
  u.hash = ''
  return u.toString()
}

/** Begin an external sign-in. Resolves only if the navigation did not happen. */
export async function startExternalSignIn(provider: string): Promise<void> {
  // THE DESKTOP CANNOT BE A REDIRECT TARGET. Google will not send a browser to a packaged app, so
  // the host opens the system browser and catches the code on a loopback listener (RFC 8252) —
  // and the daemon, not this renderer, ends up holding the session, exactly as it does for a
  // password sign-in. This resolves when that is done rather than navigating anywhere.
  if (isDesktop) {
    const answer = await hostOAuthSignIn(provider, accountsUrl())
    if (!answer) {
      throw new Error('this version of the desktop app cannot sign in with a provider - use email')
    }
    const b = answer.body as { state?: string; error?: string }
    if (b?.state !== 'ok') {
      throw new Error(String(b?.error || `sign-in failed (HTTP ${answer.status || 'daemon away'})`))
    }
    // Same tail as the password path: the runtime is the holder, this reads it back and rebuilds
    // the socket, because the credential only reaches the daemon when the socket is remade.
    await refreshMachine()
    if (!getSession()) throw new Error('signed in, but the runtime returned no session - try again')
    gateway.reconnect()
    hardRefresh()
    return
  }
  const base = accountsUrl()
  if (!base) throw new Error('this deployment has no accounts service to sign in to')
  const r = await fetch(`${base}/auth/authorize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, redirect_uri: oauthRedirectUri() })
  })
  const d = (await r.json().catch(() => ({}))) as {
    authorization_url?: string
    state?: string
    code_verifier?: string
    detail?: string
  }
  if (!r.ok || !d.authorization_url || !d.state) {
    throw new Error(String(d.detail || `could not start sign-in (HTTP ${r.status})`))
  }
  // sessionStorage because the browser LEAVES this page and comes back to a fresh load; a value
  // held in a variable would not survive the trip. One use, then gone.
  const pending: PendingOAuth = {
    provider,
    state: d.state,
    verifier: String(d.code_verifier || '')
  }
  try {
    sessionStorage.setItem(OAUTH_FLOW_KEY, JSON.stringify(pending))
  } catch {
    /* storage blocked: the callback will refuse on the state check, which is the honest failure */
  }
  location.assign(d.authorization_url)
}

/** Is this load the provider returning? `{code, state}` or null. */
export function oauthReturn(): { code: string; state: string } | null {
  try {
    const u = new URL(location.href)
    const code = u.searchParams.get('code') || ''
    const state = u.searchParams.get('state') || ''
    return code && state ? { code, state } : null
  } catch {
    return null
  }
}

/** Take `?code=&state=` out of the address bar. A spent code in history is replayable-looking
 *  noise, and leaving it means a refresh re-runs a dead exchange. */
export function clearOauthReturn(): void {
  try {
    const u = new URL(location.href)
    for (const k of ['code', 'state', 'scope', 'authuser', 'prompt', 'session_state', 'hd']) {
      u.searchParams.delete(k)
    }
    history.replaceState(null, '', u.toString())
  } catch {
    /* no history API here */
  }
}

/**
 * Finish an external sign-in and become that account.
 *
 * The tail is deliberately identical to `enter()`'s web branch — store the pair, rebuild the
 * socket, hard refresh — because the credential means the same thing however it was obtained,
 * and a second way of "becoming signed in" is how two paths drift.
 */
export async function finishExternalSignIn(args: {
  code: string
  state: string
}): Promise<Session> {
  let pending: PendingOAuth | null = null
  try {
    const raw = sessionStorage.getItem(OAUTH_FLOW_KEY)
    sessionStorage.removeItem(OAUTH_FLOW_KEY)
    pending = raw ? (JSON.parse(raw) as PendingOAuth) : null
  } catch {
    pending = null
  }
  // Checked HERE as well as on the server: the server refuses a state it never issued, and this
  // refuses one THIS TAB did not start — a callback URL pasted into an open session.
  if (!pending || pending.state !== args.state) {
    throw new Error('this sign-in did not start in this tab — press the button again')
  }
  const base = accountsUrl()
  if (!base) throw new Error('this deployment has no accounts service to sign in to')
  const r = await fetch(`${base}/auth/callback`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code: args.code,
      state: args.state,
      code_verifier: pending.verifier || undefined,
      cookie: true,
      client_id: 'agentd-web'
    })
  })
  const d = (await r.json().catch(() => ({}))) as {
    access_token?: string
    account_id?: string
    email?: string
    detail?: string
    error?: string
  }
  if (!r.ok || !d.access_token) {
    throw new Error(String(d.detail || d.error || `sign-in failed (HTTP ${r.status})`))
  }
  // THROUGH THE TOKEN MANAGER, exactly as the password path does. It owns storage, the renewal
  // timer and the subscribers; writing the session anywhere else would leave a signed-in window
  // that never renews and never notifies anything.
  //
  // `refreshToken` is empty ON PURPOSE: cookie mode put that half in an HttpOnly cookie on the
  // accounts host, which is the whole point of asking for it. The manager already treats an empty
  // refresh token as a legitimate state (see TokenPair), and `restoreSession` reads the cookie
  // back through the same door the password path uses.
  const pair = {
    accessToken: String(d.access_token),
    refreshToken: '',
    expiresAt: accessTokenExpiry(String(d.access_token)),
    accountId: String(d.account_id || ''),
    email: String(d.email || '')
  }
  tokens().replace(pair)
  const s: Session = { token: pair.accessToken, accountId: pair.accountId, email: pair.email }
  gateway.reconnect()
  hardRefresh()
  return s
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
// ON DESKTOP, NONE OF THE MANAGER RUNS. The runtime holds the one refresh token and renews it
// (platform_session.py); this renderer asks over local HTTP like every other window on the
// machine, and the push chain that fed app windows is gone because nothing needs feeding.
// The manager — and its cookie mode — is the WEB's machinery, where there is no runtime to ask.
if (!isDesktop) {
  configureTokens(() => accountsUrl())
  // Every change to the credential — sign-in, renewal, sign-out — re-renders whoever is showing
  // it. Subscribing HERE rather than in each caller is what makes the projection trustworthy.
  onTokens(announce)
} else {
  // Sign-in state is MACHINE state: when any window signs in or out, the runtime broadcasts
  // `auth.changed` to every connection, and this one re-reads and re-renders.
  gateway.on('auth.changed', () => void refreshMachine())
}
cached = project()

export function getSession(): Session | null {
  return cached
}

/** THE WINDOW STARTS OVER on any sign-in or sign-out. Rebuilding the socket moves the daemon to
 *  the new identity; it does nothing for the state this page already holds — the rail, the open
 *  conversations, the balance — and every piece of it that did not re-read kept the previous
 *  user's. Two accounts signed in one after the other saw each other's history until F5, so the
 *  honest response to the identity changing is F5. */
function hardRefresh(): void {
  if (typeof window !== 'undefined' && window.location) window.location.reload()
}

export function signOut(): void {
  if (isDesktop) {
    // The RUNTIME forgets and revokes (every window on this machine signs out together —
    // identity is a fact about the machine now); the socket rebuild drops this connection's
    // inherited account.
    void (async () => {
      await askRuntime('/auth/logout')
      machine = null
      announce()
      gateway.reconnect()
  hardRefresh()
    })()
    return
  }
  // Revokes server-side too, not just locally. Forgetting a 30-day refresh token without telling
  // the server leaves a live credential on a machine the user may have just stopped trusting.
  //
  // AND THE RELOAD WAITS FOR IT. clearTokens forgets the local copy, then POSTs /auth/logout —
  // and in cookie mode that POST is the only thing that clears the refresh cookie. Reloading
  // without awaiting it cut the request off, the cookie survived, and the fresh page restored
  // the session from it: Sign out appeared to do nothing. The SDK's own sign-out (identity.ts,
  // what every agent window uses) reloads only once the sign-out resolves; this is the same rule.
  void (async () => {
    try {
      await clearTokens()
    } finally {
      // Same reason as sign-in: the credential lives in the socket url, so the daemon keeps
      // treating this client as the old account until the socket is rebuilt without it.
      gateway.reconnect()
      hardRefresh()
    }
  })()
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
  if (isDesktop) {
    // The RUNTIME does the exchanging and becomes the holder; the answer here is only "ok or
    // why not". Credentials ride headers for the same reason the runtime's own do — its HTTP
    // surface takes no body, and a query string lands in logs.
    const r = await askRuntime('/auth/login', {
      'X-Auth-Email': args.email.trim().toLowerCase(),
      'X-Auth-Password': args.password,
      ...(args.signup ? { 'X-Auth-Signup': '1' } : {})
    })
    const b = r.body as { state?: string; error?: string }
    if (b?.state !== 'ok') {
      // REJECTS with the server's own message so the form has something to show — a failed
      // attempt must never resolve to "signed out".
      throw new Error(String(b?.error || `sign-in failed (HTTP ${r.status || 'daemon away'})`))
    }
    await refreshMachine()
    const s = getSession()
    if (!s) throw new Error('signed in, but the runtime returned no session — try again')
    gateway.reconnect()
    hardRefresh()
    return s
  }
  const p = await tokens().login(args)
  const s: Session = {
    token: p.accessToken,
    accountId: p.accountId,
    email: p.email || args.email.trim().toLowerCase()
  }
  // The credential lives in the socket url, so the daemon goes on treating this client as whoever
  // it was until the socket is rebuilt with the new one.
  gateway.reconnect()
  hardRefresh()
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
  if (isDesktop) {
    // The runtime already holds (and lazily renews) the machine's session; this is a read. The
    // one write is the legacy migration: a pre-runtime refresh token still on this machine is
    // handed over once and cleared.
    await refreshMachine()
    if (!machine) await migrateLegacySession()
    return getSession()
  }
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
  if (isDesktop) {
    // Asked fresh every time: the runtime caches and single-flights the real work, so this is
    // one local HTTP hop, and the token that comes back is never near death (RENEW_MARGIN).
    const r = await askRuntime('/auth/token')
    const b = r.body as { state?: string; accessToken?: string }
    const live = b?.state === 'ok' ? String(b.accessToken || '') : ''
    return live || cached?.token || ''
  }
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
  if (isDesktop) {
    // The runtime's typed answer maps one-to-one: it already distinguishes "the credential is
    // dead" from "the service is away", which is the whole point of this function's contract.
    const r = await askRuntime('/auth/token')
    const state = String((r.body as { state?: string })?.state || '')
    if (state === 'ok') return 'valid'
    if (state === 'signed_out' || state === 'session_expired') return 'invalid'
    return 'unknown'
  }
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
  // No return URL: BillingClient sends the rail to the accounts service's own neutral
  // "checkout finished" page. Passing this window's href here is what used to drag the session
  // token through the rail's redirect and reopen the app in a browser tab — the initiating
  // window learns the outcome from the balance (awaitGrant), never from the returning tab.
  return shop.buy(productId, '', orgId)
}

/** Wait for a checkout begun with `purchase` to grant, then ring the credits bus. */
export function awaitGrant(): Promise<boolean> {
  return shop.awaitGrant({})
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
