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
import { authUrl, fetchToken, identity } from './identity'
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

export async function authStatus(opts: AuthOptions = {}): Promise<AuthState> {
  const status = await platformStatus(opts)
  const canUseCloud = !!status.canUseCloud
  // The runtime's answer, not a stored session's: a window has nothing of its own to consult.
  const tok = await fetchToken(opts)
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
  if (!r.ok || d.state !== 'ok') {
    throw new Error(String(d.error || `sign-in failed (HTTP ${r.status})`))
  }
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
