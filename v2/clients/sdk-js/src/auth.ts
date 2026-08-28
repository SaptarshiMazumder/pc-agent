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
import { effectiveMode, loadMode, saveMode, type RunMode } from './session'

export interface AuthState {
  /** Does this daemon have an accounts service at all? (BYOK installs: no.) */
  available: boolean
  signedIn: boolean
  email: string
  accountId: string
  /** Which keys pay for THIS connection's model calls. */
  mode: RunMode
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
    mode: effectiveMode(opts.storageKey, signedIn, canUseCloud),
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
  const d = (await r.json().catch(() => ({}))) as { state?: string; error?: string }
  if (!r.ok || d.state !== 'ok') {
    throw new Error(String(d.error || `sign-in failed (HTTP ${r.status})`))
  }
  return authStatus(opts)
}

/** Forget the MACHINE's session. Every window on this daemon signs out together — identity is a
 *  fact about the machine now, not about a window. */
export async function authLogout(opts: AuthOptions = {}): Promise<AuthState> {
  await fetch(authUrl('/auth/logout', opts), { cache: 'no-store' }).catch(() => {
    // The runtime being unreachable does not keep a window "signed in" — status will answer.
  })
  saveMode(null, opts.storageKey)
  return authStatus(opts)
}

/** Choose which keys pay for THIS client's model calls. Other clients are unaffected. */
export async function setRunMode(mode: RunMode, opts: AuthOptions = {}): Promise<AuthState> {
  if (mode === 'cloud' && !(await identity(opts).accessToken())) {
    throw new Error('sign in first — Cloud mode meters model calls to your account')
  }
  saveMode(mode, opts.storageKey)
  opts.client?.reconnect() // the daemon reads the mode when the socket opens
  return authStatus(opts)
}

/** Re-exported so a caller that only imports auth.ts can still read the stored choice. */
export { loadMode }
