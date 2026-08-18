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
import {
  type RunMode,
  accessTokenAccount,
  accessTokenExpiry,
  effectiveMode,
  loadSession,
  saveMode,
  saveSession
} from './session'

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
  /**
   * Does this daemon DEMAND an account, or merely offer one?
   *
   * `available` says an accounts service exists. That is not the same question, and conflating
   * them is why a desktop daemon — which accepts the machine token and requires no account at
   * all — still put a sign-in form in front of every window. Only the daemon knows: it is an
   * explicit hosted opt-in, not something a client can infer from a configured URL.
   */
  required: boolean
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

// 15s was not enough for SIGNUP. The accounts service hashes at 200k PBKDF2 rounds and then
// writes to a network filesystem, which on a small container measures in the tens of seconds —
// so a correct signup was being reported to the user as "login timed out". Sign-in itself is
// sub-second; this ceiling exists for the slow path, and the honest fix for THAT is on the
// server (see the accounts service), not a bigger number here.
const DEFAULT_TIMEOUT = 45000

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
    canUseCloud,
    // Absent on an older daemon. Defaulting to TRUE keeps the gate exactly as it was there —
    // a client that guessed "not required" against a daemon that requires it would show no
    // login and then fail every call with no explanation.
    required: status.signInRequired !== false
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

  // `access_token` is the field a current server returns. `token`/`session` were the OPAQUE
  // session this platform issued before signed tokens, kept here only so an SDK build newer than
  // its server still works — the reverse (reading only `token`) is what broke every agent app's
  // sign-in the day the server stopped issuing it, with the server returning 200 the whole time.
  const token = String(login?.access_token || login?.token || login?.session || '')
  if (!token) throw new Error('the accounts server returned no session token')
  saveSession(
    {
      token,
      email: String(login?.email || email),
      accountId: String(login?.account_id || ''),
      refreshToken: String(login?.refresh_token || '') || undefined,
      expiresAt: login?.expires_in
        ? Date.now() + Number(login.expires_in) * 1000
        : undefined
    },
    opts.storageKey
  )
  opts.client?.reconnect() // the daemon reads identity when the socket opens
  return authStatus(opts)
}

/**
 * Renew the access token from the stored refresh token. Returns the new access token, or '' when
 * there is nothing to renew with (an older session, or one the server has revoked).
 *
 * WHY AN APP WINDOW NEEDS THIS AT ALL. Its credential arrives on the page URL and lasts about ten
 * minutes; the socket then reconnects at some point and presents it again. An expired token is not
 * refused — the daemon accepts the connection ANONYMOUSLY — so the page keeps working while
 * quietly losing access to everything the account owns. Renewing on a timer is what stops that,
 * and it fails CLOSED: a rejected refresh clears the session so the UI shows a sign-in form
 * instead of an inexplicably empty app.
 */
export async function authRefresh(opts: AuthOptions = {}): Promise<string> {
  const stored = loadSession(opts.storageKey)
  if (!stored?.refreshToken) return ''
  const status = await platformStatus(opts)
  const accountsUrl = String(status.accountsUrl || '').replace(/\/$/, '')
  if (!accountsUrl) return ''
  try {
    const next = await post(
      `${accountsUrl}/auth/refresh`,
      { refresh_token: stored.refreshToken },
      opts.timeoutMs ?? DEFAULT_TIMEOUT,
      'refresh'
    )
    const token = String(next?.access_token || '')
    if (!token) return ''
    saveSession(
      {
        token,
        email: stored.email,
        accountId: stored.accountId,
        refreshToken: String(next?.refresh_token || '') || stored.refreshToken,
        expiresAt: next?.expires_in ? Date.now() + Number(next.expires_in) * 1000 : undefined
      },
      opts.storageKey
    )
    // The daemon reads identity when the socket OPENS, so a renewed token only takes effect on
    // the next connection. Reconnecting here is what makes the renewal actually mean something.
    opts.client?.reconnect()
    return token
  } catch {
    // A refresh that the server REFUSED is terminal: the family is gone or expired, and retrying
    // forever with a dead credential is how a page ends up anonymous without saying so.
    saveSession(null, opts.storageKey)
    opts.client?.reconnect()
    return ''
  }
}

/**
 * Keep this window's access token fresh for as long as the page is open.
 *
 * Returns a stop function. Renews at 80% of the token's life; falls back to a 5-minute poll for a
 * session stored before `expiresAt` existed.
 */
/**
 * Accept access tokens pushed down by the desktop shell. Returns an unsubscribe.
 *
 * An app window opened from the shell gets its credential on the launch URL and holds NO refresh
 * token — deliberately, because an agent app is third-party code and a refresh token is a 30-day
 * credential for the user's whole account. So it cannot renew itself; the shell, which does hold
 * the refresh token, mints short-lived access tokens and hands them down (see the desktop's
 * src/preload/app.ts).
 *
 * Each pushed token is stored AND applied to the live socket via `auth.update`, so a running
 * agent is never interrupted by a renewal. A no-op anywhere without the shell bridge — a browser
 * tab, or an app window signed in through its own form (which has a real refresh token and uses
 * `startAuthRenewal` instead).
 */
export function acceptHostTokens(opts: AuthOptions = {}): () => void {
  const host = (globalThis as { agentdHost?: { onAccessToken(cb: (t: string) => void): () => void } })
    .agentdHost
  if (!host?.onAccessToken) return () => undefined
  return host.onAccessToken((token) => {
    if (!token) return
    const stored = loadSession(opts.storageKey)
    // WHOSE TOKEN IS THIS? The shell broadcasts to EVERY open app window at once — it does not
    // know, and cannot know, that one of them is signed in as somebody else. A window that signed
    // in through its own form is a DIFFERENT account on purpose (see session.ts: one daemon, many
    // sockets, many answers), and taking the shell's token here would end that quietly and
    // badly: storage would hold this account's email and refresh token beside the OTHER
    // account's access token, `auth.update` would be refused for mismatching the connection, and
    // the reconnect in the catch below would then present the token we had just stored — landing
    // the window on the shell's account while still displaying this one's email.
    //
    // An empty stored accountId is the ordinary case, not an exception: a window opened BY the
    // shell adopts its credential from the launch url and records no account (client.ts
    // `fromPage`), so it has nothing to disagree with and accepts every push, exactly as before.
    // An unreadable token fails CLOSED — a credential we cannot attribute is not one to adopt.
    if (stored?.accountId && accessTokenAccount(token) !== stored.accountId) return
    saveSession(
      {
        token,
        email: stored?.email || '',
        accountId: stored?.accountId || '',
        refreshToken: stored?.refreshToken,
        // From the token's own `exp` rather than left empty. The shell still owns the SCHEDULE —
        // it decides when to push next — but the window has to know when what it is holding dies,
        // or an unattended one goes on presenting a spent credential (see `spent` in session.ts).
        expiresAt: accessTokenExpiry(token) || undefined
      },
      opts.storageKey
    )
    // Swap the credential on the OPEN socket rather than reconnecting: a reconnect would drop an
    // in-flight run, which is the exact thing a silent background renewal must never do.
    void opts.client
      ?.request('auth.update', { accessToken: token })
      .catch(() => opts.client?.reconnect())
  })
}

export function startAuthRenewal(opts: AuthOptions = {}): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  const tick = async (): Promise<void> => {
    const stored = loadSession(opts.storageKey)
    if (!stored?.refreshToken) return
    const life = (stored.expiresAt || 0) - Date.now()
    if (life > 0 && life < 600_000) await authRefresh(opts)
    const next = stored.expiresAt ? Math.max(30_000, life * 0.8) : 300_000
    timer = setTimeout(() => void tick(), next)
  }
  void tick()
  return () => {
    if (timer) clearTimeout(timer)
  }
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
