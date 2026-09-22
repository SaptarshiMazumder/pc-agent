/* Sign-in state and the three account operations, for a window: ALL OF IT ASKS THE RUNTIME.
 *
 * The window never talks to the accounts service about its own identity and never holds a
 * credential. `authLogin` posts the form to the runtime's local `/auth/login`; the runtime
 * exchanges it with the accounts service and keeps the session (platform_session.py — ONE
 * holder, ONE refresher, per machine). `authStatus` reads back the fact. Every call is a GET
 * with the operation's inputs in request HEADERS: the runtime's HTTP surface is the websockets
 * handshake hook, which refuses any other method at the request line and never receives a body
 * — and a password in a query string would land in logs and history. Headers do neither.
 *
 * The shapes (`AuthState`, the function signatures) are unchanged from the era when this file
 * did the exchanging itself, so `common/auth`, the gate, the profile menu and every agent
 * compile as they are. What changed is where the work happens — see identity.ts for the whole
 * story of what was deleted and why.
 */

import type { AgentdClient } from './client'
import { authUrl, forgetIdentityCache, identity } from './identity'
import { platformStatus, type DaemonOptions } from './platform-status'
import { loadMode, saveMode, type RunMode } from './session'

export interface AuthState {
  /** Does this daemon have an accounts service at all? (BYOK installs: no.) */
  available: boolean
  signedIn: boolean
  email: string
  accountId: string
  /** Which keys pay for model calls — the DAEMON's persisted answer, same in every window. */
  mode: RunMode
  /** Is the mode fixed (no toggle)? True on hosted, where cloud is the only runnable option. */
  modeLocked: boolean
  canUseCloud: boolean
  /** Must somebody sign in before this app may run? The daemon's answer; `<Gate>` reads it. */
  required: boolean
}

export interface AuthOptions extends DaemonOptions {
  client?: AgentdClient
  /** Accepted for compatibility; per-window sessions are gone. */
  storageKey?: string
}

/** One way to sign in, as the accounts service advertises it. `kind: "password"` is the email
 *  form; every other entry is an external provider with a button. */
export interface AuthProvider {
  id: string
  label: string
  kind: 'password' | 'oidc' | string
}

/** WHERE THE STASHED FLOW LIVES between the redirect out and the redirect back.
 *
 *  `sessionStorage`, not memory: the browser LEAVES this page to visit the provider and comes
 *  back to a fresh load, so anything held in a variable is gone by the time the code arrives.
 *  Per-tab and cleared by the browser, which is right for a value that is useless after one use.
 *
 *  What is stashed is the `state` we were issued and — for a public client — the PKCE verifier.
 *  Neither is a credential on its own: the verifier proves the code belongs to the flow THIS tab
 *  started, and that is exactly what stops an intercepted code being redeemed by anyone else. */
const FLOW_KEY = 'agentd.oauth.flow'

interface StashedFlow {
  provider: string
  state: string
  verifier: string
  redirectUri: string
}

function stashFlow(flow: StashedFlow): void {
  try {
    sessionStorage.setItem(FLOW_KEY, JSON.stringify(flow))
  } catch {
    /* private window with storage blocked — the callback will report the mismatch honestly */
  }
}

function takeFlow(): StashedFlow | null {
  try {
    const raw = sessionStorage.getItem(FLOW_KEY)
    sessionStorage.removeItem(FLOW_KEY) // one use, whatever happens next
    return raw ? (JSON.parse(raw) as StashedFlow) : null
  } catch {
    return null
  }
}

/** The accounts service's own discovery document — public, no credential. */
async function accountsBase(opts: AuthOptions): Promise<string> {
  const base = String((await platformStatus(opts)).accountsUrl || '').replace(/\/$/, '')
  if (!base) throw new Error('this deployment has no accounts service to sign in to')
  return base
}

/**
 * Every way to sign in to THIS deployment, from the accounts service's discovery document.
 *
 * DATA, NOT A HARDCODED BUTTON. The server decides which providers exist (four environment
 * variables per provider); this returns what it said, and the sign-in card renders one button per
 * entry. Adding Microsoft is therefore a server change and zero client releases — the same rule
 * the codebase already follows for models and tools.
 *
 * An empty list is the honest answer for a BYOK build with no accounts service, and callers
 * render nothing rather than a dead button.
 */
/** May the card offer "Create an account"? False where the deployment has closed password
 *  sign-up (production offers Google only, because a password sign-up verifies nothing). True
 *  when the document does not say — an older accounts service, or none at all, is the world as
 *  it was. */
export async function authSignupOpen(opts: AuthOptions = {}): Promise<boolean> {
  let base: string
  try {
    base = await accountsBase(opts)
  } catch {
    return true
  }
  try {
    const r = await fetch(`${base}/.well-known/agentd-platform`, { cache: 'no-store' })
    if (!r.ok) return true
    const d = (await r.json()) as { password_signup?: unknown }
    return d.password_signup !== false
  } catch {
    return true
  }
}

export async function authProviders(opts: AuthOptions = {}): Promise<AuthProvider[]> {
  let base: string
  try {
    base = await accountsBase(opts)
  } catch {
    return []
  }
  try {
    const r = await fetch(`${base}/.well-known/agentd-platform`, { cache: 'no-store' })
    if (!r.ok) return []
    const d = (await r.json()) as { providers?: AuthProvider[] }
    return Array.isArray(d.providers) ? d.providers : []
  } catch {
    return []
  }
}

/**
 * Start an external sign-in: ask the accounts service for the provider's authorization URL, stash
 * what proves the round trip is ours, and hand back the URL for the caller to visit.
 *
 * IT DOES NOT NAVIGATE. The caller decides how the browser gets there — a web page assigns
 * `location`, and a desktop shell opens the system browser, because an installed app must never
 * put the provider's password form inside its own window. One function, both shells.
 *
 * `redirectUri` MUST be one the provider has been told about, and it is echoed back to the token
 * endpoint at exchange time — a mismatch there is the single most common cause of a flow that
 * works locally and fails in production.
 */
export async function authAuthorize(
  args: { provider: string; redirectUri: string },
  opts: AuthOptions = {},
): Promise<string> {
  const base = await accountsBase(opts)
  const r = await fetch(`${base}/auth/authorize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: args.provider, redirect_uri: args.redirectUri }),
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
  stashFlow({
    provider: args.provider,
    state: d.state,
    verifier: String(d.code_verifier || ''),
    redirectUri: args.redirectUri,
  })
  return d.authorization_url
}

/** Is this page load the provider sending the browser back? `{code, state}` or null. */
export function oauthCallbackParams(href?: string): { code: string; state: string } | null {
  try {
    const u = new URL(href || location.href)
    const code = u.searchParams.get('code') || ''
    const state = u.searchParams.get('state') || ''
    return code && state ? { code, state } : null
  } catch {
    return null
  }
}

/**
 * Finish an external sign-in: redeem the code for a session.
 *
 * COOKIE MODE, like the password path. The refresh half becomes an HttpOnly Set-Cookie on the
 * accounts host rather than a value this page holds — which is what `fetchCookieToken` already
 * reads to renew, so nothing downstream needs to know which door was used.
 *
 * THE STATE IS CHECKED HERE TOO, not only on the server. The server refuses a `state` it never
 * issued; this refuses one THIS TAB did not start, which is the case the server cannot see — a
 * callback URL pasted or linked into a logged-in session.
 */
export async function authCallback(
  args: { code: string; state: string },
  opts: AuthOptions = {},
): Promise<AuthState> {
  const flow = takeFlow()
  if (!flow || flow.state !== args.state) {
    throw new Error('this sign-in did not start in this tab — press the button again')
  }
  const base = await accountsBase(opts)
  const r = await fetch(`${base}/auth/callback`, {
    method: 'POST',
    credentials: 'include', // the Set-Cookie is the session
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code: args.code,
      state: args.state,
      code_verifier: flow.verifier || undefined,
      cookie: true,
      client_id: 'agentd-web',
    }),
  })
  const d = (await r.json().catch(() => ({}))) as { detail?: string; error?: string }
  if (!r.ok) throw new Error(String(d.detail || d.error || `sign-in failed (HTTP ${r.status})`))
  // A different person may have just signed in — drop any token cached for the previous one.
  forgetIdentityCache()
  return authStatus(opts)
}

export async function authStatus(opts: AuthOptions = {}): Promise<AuthState> {
  const status = await platformStatus(opts)
  const canUseCloud = !!status.canUseCloud
  // THROUGH THE FETCHER, NOT A RAW READ. This used to call fetchToken() directly, so every hook
  // that asked — the gate, useAuth, credits, orgs, the artifact rail — was its own round trip to
  // /auth/refresh: bursts of four or five a second after a sign-in, enough to empty the accounts
  // service's per-IP budget and turn the user's next password attempt into "too many attempts".
  // The fetcher is single-flight and cached in memory only; forget() at sign-in/sign-out keeps
  // it honest, and its signature comparison is where "this is a different person" is decided —
  // so reading through it is also what makes that decision fire on every status read.
  const tok = await identity(opts).state()
  const signedIn = tok.state === 'ok'
  return {
    available: !!String(status.accountsUrl || ''),
    signedIn,
    email: (signedIn && tok.email) || '',
    accountId: (signedIn && tok.accountId) || '',
    // THE DAEMON'S answer, not a client-side guess: it reads persisted config (and forces cloud on
    // hosted). This is what fixes "the switch says Cloud but the call ran Local".
    mode: status.mode === 'local' || status.mode === 'cloud' ? status.mode : 'local',
    modeLocked: !!status.runModeLocked,
    canUseCloud,
    // Absent on an older daemon. Defaulting to TRUE keeps the gate exactly as it was there — a
    // client that guessed "not required" against a daemon that requires it would show no login
    // and then fail every call with no explanation.
    required: status.signInRequired !== false,
  }
}

/**
 * Sign in, or create the account first when `signup`.
 *
 * REJECTS on a rejected credential, carrying the server's own message ("incorrect password") so
 * a form has something to show. A failed attempt must never resolve to `signedIn: false`: the
 * caller cannot tell that apart from "signed out", and the user is left looking at a form that
 * cleared itself.
 */
export async function authLogin(
  args: { email: string; password: string; signup?: boolean },
  opts: AuthOptions = {},
): Promise<AuthState> {
  const r = await fetch(authUrl('/auth/login', opts), {
    cache: 'no-store',
    headers: {
      'X-Auth-Email': args.email,
      'X-Auth-Password': args.password,
      ...(args.signup ? { 'X-Auth-Signup': '1' } : {}),
    },
  })
  // HOSTED: the daemon has no runtime login (it serves many people, holds no machine session),
  // so /auth/login is 404 there. Sign in against the ACCOUNTS service directly, in COOKIE mode
  // — the same door the main web client uses, and the same session `fetchToken` already falls
  // back to reading (identity.ts, fetchCookieToken). Without this the builder's own sign-in form
  // posted into a 404 and the flagship web page could never authenticate. The Set-Cookie lands
  // on the accounts host, which is exactly where the token read looks for it.
  if (r.status === 404) return cookieLogin(args, opts)
  const d = (await r.json().catch(() => ({}))) as { state?: string; error?: string }
  // SAME RULE AS fetchToken: only a TYPED answer proves a runtime login exists here. 404 is what
  // a hosted DAEMON returns, but a hosted window is served from the SPA's origin, where this POST
  // meets nginx instead — a 405 (no POST on a static route), or the catch-all's HTML at 200.
  // Keying the fallback on 404 alone turned those into "sign-in failed (HTTP 405)" and never
  // tried the accounts cookie, which is the door that actually works there.
  if (typeof d.state !== 'string') return cookieLogin(args, opts)
  if (!r.ok || d.state !== 'ok') {
    throw new Error(String(d.error || `sign-in failed (HTTP ${r.status})`))
  }
  // The credential just changed — drop any token cached for the PREVIOUS account so orgs/credits
  // and every identity-based read resolve as who is signed in now, not who was a moment ago.
  forgetIdentityCache()
  return authStatus(opts)
}

/** Hosted sign-in: create the account if asked, then log in COOKIE-mode against accounts so the
 *  browser holds the refresh cookie and `fetchCookieToken` can renew from it. */
async function cookieLogin(
  args: { email: string; password: string; signup?: boolean },
  opts: AuthOptions,
): Promise<AuthState> {
  const base = String((await platformStatus(opts)).accountsUrl || '').replace(/\/$/, '')
  if (!base) throw new Error('this deployment has no accounts service to sign in to')
  const email = args.email.trim().toLowerCase()
  if (args.signup) {
    const s = await fetch(`${base}/signup`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      credentials: 'include',
      cache: 'no-store',
      body: JSON.stringify({ email, password: args.password }),
    })
    if (!s.ok) {
      const d = (await s.json().catch(() => ({}))) as { detail?: string; error?: string }
      throw new Error(String(d.detail || d.error || `sign-up failed (HTTP ${s.status})`))
    }
  }
  const r = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    credentials: 'include',
    cache: 'no-store',
    body: JSON.stringify({ email, password: args.password, client_id: 'agent-window', cookie: true }),
  })
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))) as { detail?: string; error?: string }
    throw new Error(String(d.detail || d.error || `sign-in failed (HTTP ${r.status})`))
  }
  forgetIdentityCache() // new cookie account — clear the previous user's cached token (see authLogin)
  return authStatus(opts)
}

/** Forget the MACHINE's session. Every window on this daemon signs out together — identity is a
 *  fact about the machine now, not about a window. */
export async function authLogout(opts: AuthOptions = {}): Promise<AuthState> {
  const r = await fetch(authUrl('/auth/logout', opts), { cache: 'no-store' }).catch(() => null)
  // HOSTED: no runtime logout either (see authLogin). Clear the accounts cookie directly so the
  // browser stops being able to renew — otherwise "sign out" would drop the run mode but leave a
  // live cookie that silently signs the next call back in.
  if (!r || r.status === 404) {
    const status = await platformStatus(opts).catch(() => ({}) as Record<string, any>)
    const base = String(status.accountsUrl || '').replace(/\/$/, '')
    if (base) {
      await fetch(`${base}/auth/logout`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'include',
        cache: 'no-store',
        body: JSON.stringify({ cookie: true }),
      }).catch(() => {
        // Unreachable accounts does not keep a window signed in — status will answer.
      })
    }
  }
  saveMode(null, opts.storageKey)
  // Signed out — forget the cached access token immediately, so nothing keeps reading as the
  // account that just left for the ~150s the cache would otherwise hold it.
  forgetIdentityCache()
  return authStatus(opts)
}

/** Choose which keys pay for THIS client's model calls. Other clients are unaffected. */
export async function setRunMode(mode: RunMode, opts: AuthOptions = {}): Promise<AuthState> {
  if (mode === 'cloud' && !(await identity(opts).accessToken())) {
    throw new Error('sign in first — Cloud mode meters model calls to your account')
  }
  // PERSIST on the daemon (config.set), like every other setting — so the choice is the SAME in
  // every window, not a per-window localStorage value. The daemon re-resolves the connection's
  // billing when the socket reopens, so a reconnect carries it.
  await opts.client?.request('config.set', { patch: { run_mode: mode } })
  opts.client?.reconnect()
  return authStatus(opts)
}

/** Re-exported so a caller that only imports auth.ts can still read the stored choice. */
export { loadMode }
