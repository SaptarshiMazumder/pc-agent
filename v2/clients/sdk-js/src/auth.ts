/**
 * Sign-in — ORDINARY HTTP, from the client, exactly like any web app.
 *
 *   GET  <daemon>/platform/status     → where the accounts service is
 *   POST <accountsUrl>/signup         (only when creating)
 *   POST <accountsUrl>/login          → a session token
 *   store it, reconnect
 *
 * The daemon is not in the middle of this. It answers one question — "where do people sign in?" —
 * and is then told the answer on the next connection.
 *
 * WHY NOT THROUGH THE DAEMON. It was, briefly: three socket methods, with the daemon performing
 * the exchange and keeping the token. That put ONE session on the machine, and one session cannot
 * serve two people — the second to sign in overwrote the first, signing out signed out everybody,
 * and any way to read the token back handed one user another's credential. Routing it through a
 * socket bought nothing this does not, and cost that.
 *
 * SO THE CLIENT DECIDES BOTH FACTS: who it is, and which keys pay. Both travel on the connection
 * (`?session=`, `?mode=`), which is why a hundred users on one daemon is a hundred sockets each
 * answering for itself.
 *
 * CHANGING EITHER RECONNECTS. The daemon reads them when the socket opens, so a sign-in that did
 * not reconnect would leave it still seeing the old answer.
 */

import { AgentdClient } from './client'
import { type RunMode, effectiveMode, loadSession, saveMode, saveSession } from './session'

export interface AuthState {
  /** Is an accounts service configured on this daemon? false => no sign-in to offer. */
  available: boolean
  /** Is THIS client signed in? */
  signedIn: boolean
  email: string
  accountId: string
  /** Which keys this client's model calls run on. */
  mode: RunMode
  /** Is there a Cloud to switch to on this build? */
  canUseCloud: boolean
}

export interface AuthOptions {
  /** The connected client, so a change can reconnect it and take effect at once. */
  client?: AgentdClient
  /** Daemon HTTP origin. Defaults to the page's own — an agent app is served BY the daemon. */
  origin?: string
  /** The daemon's bearer token. Defaults to `?token=` on the page URL. */
  token?: string
  timeoutMs?: number
  /** Storage key override; defaults to one derived from the agent id in the page URL. */
  storageKey?: string
}

const DEFAULT_TIMEOUT = 15000

function origin(opts: AuthOptions): string {
  if (opts.origin) return opts.origin.replace(/\/$/, '')
  if (typeof location === 'undefined') throw new Error('no origin: pass options.origin')
  return location.origin
}

function daemonToken(opts: AuthOptions): string {
  if (typeof opts.token === 'string') return opts.token
  if (typeof location === 'undefined') return ''
  try {
    return new URL(location.href).searchParams.get('token') || ''
  } catch {
    return ''
  }
}

async function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: any
  const guard = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms}ms`)), ms)
  })
  try {
    return await Promise.race([p, guard])
  } finally {
    clearTimeout(timer)
  }
}

/** The daemon's own view: where sign-in lives, and whether a proxy exists to switch to. */
async function platformStatus(opts: AuthOptions): Promise<Record<string, any>> {
  const u = new URL('/platform/status', `${origin(opts)}/`)
  const token = daemonToken(opts)
  if (token) u.searchParams.set('token', token)
  const r = await withTimeout(
    fetch(u.toString(), { cache: 'no-store' }),
    opts.timeoutMs ?? DEFAULT_TIMEOUT,
    'platform status'
  )
  if (!r.ok) throw new Error(`platform status failed (HTTP ${r.status})`)
  return (await r.json()) as Record<string, any>
}

async function post(url: string, body: unknown, timeoutMs: number, what: string): Promise<any> {
  const r = await withTimeout(
    fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body)
    }),
    timeoutMs,
    what
  )
  const text = await r.text()
  let data: any = {}
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    /* reported through the status check below */
  }
  if (!r.ok) {
    throw new Error(String(data?.detail || data?.error || `${what} failed (HTTP ${r.status})`))
  }
  return data
}

/** What this client is, right now: its own state, plus what the daemon offers. */
export async function authStatus(opts: AuthOptions = {}): Promise<AuthState> {
  const status = await platformStatus(opts)
  const stored = loadSession(opts.storageKey)
  const canUseCloud = !!status.canUseCloud
  return {
    available: !!String(status.accountsUrl || ''),
    signedIn: !!stored,
    email: stored?.email || '',
    accountId: stored?.accountId || '',
    mode: effectiveMode(opts.storageKey, !!stored, canUseCloud),
    canUseCloud
  }
}

/**
 * Sign in, or create the account first when `signup`.
 *
 * REJECTS on a rejected credential, carrying the accounts service's own message ("incorrect
 * password") so a form has something to show. A failed attempt must never resolve to
 * `signedIn: false`: the caller cannot tell that apart from "signed out", and the user is left
 * looking at a form that cleared itself.
 */
export async function authLogin(
  args: { email: string; password: string; signup?: boolean },
  opts: AuthOptions = {}
): Promise<AuthState> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT
  const status = await platformStatus(opts)
  const accountsUrl = String(status.accountsUrl || '').replace(/\/$/, '')
  if (!accountsUrl) throw new Error('this daemon has no accounts service configured')

  const email = args.email.trim().toLowerCase()
  if (args.signup) {
    await post(`${accountsUrl}/signup`, { email, password: args.password }, timeoutMs, 'signup')
  }
  const login = await post(
    `${accountsUrl}/login`,
    { email, password: args.password },
    timeoutMs,
    'login'
  )

  const token = String(login?.token || login?.session || '')
  if (!token) throw new Error('the accounts server returned no session token')
  saveSession(
    { token, email: String(login?.email || email), accountId: String(login?.account_id || '') },
    opts.storageKey
  )
  opts.client?.reconnect() // the daemon reads identity when the socket opens
  return authStatus(opts)
}

/** Forget this client's session. Other windows keep theirs — each holds its own. */
export async function authLogout(opts: AuthOptions = {}): Promise<AuthState> {
  saveSession(null, opts.storageKey)
  saveMode(null, opts.storageKey)
  opts.client?.reconnect()
  return authStatus(opts)
}

/** Choose which keys pay for THIS client's model calls. Other clients are unaffected. */
export async function setRunMode(mode: RunMode, opts: AuthOptions = {}): Promise<AuthState> {
  if (mode === 'cloud' && !loadSession(opts.storageKey)) {
    throw new Error('sign in first — Cloud mode meters model calls to your account')
  }
  saveMode(mode, opts.storageKey)
  opts.client?.reconnect()
  return authStatus(opts)
}
