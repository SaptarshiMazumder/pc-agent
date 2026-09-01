/* Identity, for a window: ASK THE RUNTIME. Nothing else.
 *
 * WHAT THIS FILE USED TO BE — and why almost all of it is gone. Every window ran its own
 * TokenManager: its own copy of the refresh token, its own 8-minute renewal timer, its own
 * storage key, machinery for adopting tokens pushed down by an opener, and a fallback that tore
 * the websocket down when a renewal push failed. N windows meant N clocks racing single-use
 * refresh-token rotation; the losers tripped the server's reuse detector and signed everybody
 * out, and one transient failure printed "the daemon restarted mid-run" at users whose daemon
 * was fine.
 *
 * THE RUNTIME IS THE ONLY SESSION HOLDER NOW (agent_runtime/infrastructure/platform_session.py).
 * It keeps the machine's one refresh token on disk and renews it single-flight, lazily. A window
 * calls `GET /auth/token` on its own origin and gets a typed answer:
 *
 *   200 ok                    -> {accessToken, expiresAt, email, accountId}
 *   401 signed_out            -> nobody is signed in on this machine
 *   401 session_expired       -> the credential is dead; sign in again
 *   503 accounts_unreachable  -> network trouble; the session is intact — keep working, retry
 *
 * "Re-auth" in a window is therefore re-reading a local value. It cannot race anything, cannot
 * be torn down by a timeout, and ten windows asking at once ride ONE refresh in the runtime.
 *
 * THE EXPORTED SURFACE KEPT ITS SHAPE. `identity(opts).accessToken()` still answers a current
 * token — credits, orgs and every caller compile unchanged — but the object behind it is a thin
 * fetcher, not a manager. The dead machinery (`acceptHostTokens`, `startAuthRenewal`,
 * `sessionKey` storage scoping) survives as inert stubs for one release so nothing breaks at
 * import time; they do nothing, which is the point.
 */

import type { AgentdClient } from './client'
import { daemonOrigin, daemonToken, platformStatus, type DaemonOptions } from './platform-status'

export interface IdentityOptions extends DaemonOptions {
  /** BINDS the client for token pushes: whenever this fetcher obtains a FRESH cookie token
   *  (hosted), it fires `auth.update` onto every bound client's open socket — the handoff that
   *  keeps a long-lived connection, and the run already in flight on it, paying with a live
   *  token. No timers: the push rides whatever ask fetched the token (status polls, credits). */
  client?: AgentdClient
  /** Accepted for compatibility; windows no longer have per-window sessions to key. */
  storageKey?: string
}

/** The typed answer from the runtime — see the module note for the four states. */
export interface TokenAnswer {
  state: 'ok' | 'signed_out' | 'session_expired' | 'accounts_unreachable'
  accessToken?: string
  expiresAt?: number
  email?: string
  accountId?: string
  retryAfterSec?: number
  /** Who answered: the runtime (desktop) or the accounts cookie session (hosted). A window
   *  uses this to know whether it must PRESENT the token on its socket — on desktop the daemon
   *  inherits the machine identity and nothing travels. */
  via?: 'runtime' | 'cookie'
}

/** Build a runtime /auth/* URL. The MACHINE TOKEN rides along (`?token=`, same slot every
 *  other daemon HTTP call uses) because the runtime requires it where one is configured — it is
 *  what keeps a hostile web page from driving these endpoints blind. Every window has it on its
 *  own launch URL. */
export function authUrl(path: string, opts: DaemonOptions = {}): URL {
  const u = new URL(path, `${daemonOrigin(opts)}/`)
  const token = daemonToken(opts)
  if (token) u.searchParams.set('token', token)
  return u
}

/** HOSTED: no machine session exists (the daemon serves many people), so the window's
 *  renewal source is the ACCOUNTS COOKIE — the httpOnly refresh token the web sign-in set in
 *  this browser. The request goes out with credentials and the browser attaches the cookie
 *  itself; this code never sees it. The accounts origin is same-site with the daemon's, which
 *  is what lets the cookie travel (identity/presentation/auth_router.py sets the flags).
 *
 *  `signed_out` covers both "no cookie" and "dead cookie": cookie mode cannot tell them apart
 *  (the cookie is unreadable here by design), and both mean the same thing to a window — show
 *  the sign-in. */
async function fetchCookieToken(opts: DaemonOptions): Promise<TokenAnswer> {
  let base = ''
  try {
    base = String((await platformStatus(opts)).accountsUrl || '').replace(/\/$/, '')
  } catch {
    return { state: 'accounts_unreachable', retryAfterSec: 15, via: 'cookie' }
  }
  if (!base) return { state: 'signed_out', via: 'cookie' }
  let r: Response
  try {
    r = await fetch(`${base}/auth/refresh`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      credentials: 'include',
      cache: 'no-store',
      body: JSON.stringify({ cookie: true, client_id: 'agent-window' }),
    })
  } catch {
    return { state: 'accounts_unreachable', retryAfterSec: 15, via: 'cookie' }
  }
  if (r.status === 401 || r.status === 403) return { state: 'signed_out', via: 'cookie' }
  if (!r.ok) return { state: 'accounts_unreachable', retryAfterSec: 30, via: 'cookie' }
  const d = (await r.json().catch(() => ({}))) as {
    access_token?: string
    expires_in?: number
    account_id?: string
    email?: string
  }
  if (!d.access_token) return { state: 'signed_out', via: 'cookie' }
  return {
    state: 'ok',
    accessToken: d.access_token,
    expiresAt: Date.now() / 1000 + Number(d.expires_in || 0),
    accountId: String(d.account_id || ''),
    email: String(d.email || ''),
    via: 'cookie',
  }
}

/** Ask the runtime for the machine's token state. The ONE identity read everything builds on.
 *  On a hosted daemon the runtime answers 404 — no machine session exists there — and the ask
 *  falls through to the accounts cookie (see fetchCookieToken). */
export async function fetchToken(opts: DaemonOptions = {}): Promise<TokenAnswer> {
  try {
    const r = await fetch(authUrl('/auth/token', opts), {
      cache: 'no-store',
    })
    if (r.status === 404) return fetchCookieToken(opts)
    const d = (await r.json().catch(() => ({}))) as TokenAnswer
    if (d && typeof d.state === 'string') return { ...d, via: 'runtime' }
    return { state: r.ok ? 'ok' : 'signed_out', via: 'runtime' }
  } catch {
    // The RUNTIME itself is unreachable — indistinguishable, for a caller, from the accounts
    // service being away: keep working, retry later. Never "signed out": that answer makes a
    // window drop a working session over a hiccup, which is the bug family this file replaced.
    return { state: 'accounts_unreachable', retryAfterSec: 15 }
  }
}

/** The thin per-window fetcher behind `identity()`. Caches the token in memory only, and only
 *  until near expiry — the runtime does all real work, so "cache" here just saves HTTP chatter
 *  between keystrokes. */
class TokenFetcher {
  private answer: TokenAnswer | null = null
  private inflight: Promise<TokenAnswer> | null = null
  private readonly clients = new Set<AgentdClient>()

  constructor(private readonly opts: DaemonOptions) {}

  /** Register a client to receive `auth.update` pushes. Idempotent. */
  bind(client: AgentdClient): void {
    this.clients.add(client)
  }

  /** A current access token, or '' when the machine is signed out / unreachable. Callers that
   *  need to know WHY ask `state()`. */
  async accessToken(): Promise<string> {
    const a = await this.state()
    return a.state === 'ok' ? a.accessToken || '' : ''
  }

  async state(): Promise<TokenAnswer> {
    const held = this.answer
    // 30s of margin under the runtime's own 120s: a token this window hands out is still alive
    // for the request it authorises.
    if (held?.state === 'ok' && (held.expiresAt || 0) * 1000 - Date.now() > 150_000) return held
    if (held?.state === 'ok' && (held.expiresAt || 0) - Date.now() / 1000 > 150) return held
    if (!this.inflight) {
      this.inflight = fetchToken(this.opts).finally(() => {
        this.inflight = null
      })
      this.inflight.then((a) => {
        const prev = this.answer
        this.answer = a
        this.push(a, prev)
      })
    }
    return this.inflight
  }

  /** THE HANDOFF. A hosted connection's identity is the token it presented — a snapshot the
   *  daemon cannot renew (it holds no refresh token for this user; the browser's cookie does).
   *  So when a genuinely NEW cookie token arrives, every bound open socket gets it via
   *  `auth.update`, which the daemon applies to the connection AND to the turn already running
   *  on it. Desktop answers come via the runtime, which renews its own connections — no push.
   *  Fire-and-forget: a socket that is closed or an older daemon just ignores it. */
  private push(a: TokenAnswer, prev: TokenAnswer | null): void {
    if (a.state !== 'ok' || a.via !== 'cookie' || !a.accessToken) return
    if (prev?.state === 'ok' && prev.accessToken === a.accessToken) return
    for (const c of this.clients) {
      void c.request('auth.update', { accessToken: a.accessToken }).catch(() => {
        /* not connected / older daemon — the reconnect resolver presents the fresh token */
      })
    }
  }

  signedIn(): boolean {
    return this.answer?.state === 'ok'
  }

  /** Compatibility shape for callers that read `current()?.email`. */
  current(): { email: string; accountId: string } | null {
    const a = this.answer
    return a?.state === 'ok'
      ? { email: a.email || '', accountId: a.accountId || '' }
      : null
  }
}

const fetchers = new Map<string, TokenFetcher>()

/** The window's identity handle. One per daemon origin; all state lives in the runtime. */
export function identity(opts: IdentityOptions = {}): TokenFetcher {
  const key = daemonOrigin(opts)
  let f = fetchers.get(key)
  if (!f) {
    f = new TokenFetcher(opts)
    fetchers.set(key, f)
  }
  if (opts.client) f.bind(opts.client)
  return f
}

/** TEST SEAM: forget cached answers (a signed-out test must not see the last test's token). */
export function resetIdentity(): void {
  fetchers.clear()
}

/* ── inert stubs — the deleted machinery's names, kept one release so imports resolve ──────── */

/** DEAD: windows have no per-window sessions to key any more. Returns a stable string for any
 *  caller still using it as a cache key. */
export function sessionKey(explicit = ''): string {
  return explicit || 'agentd.session.machine'
}

/** DEAD: openers no longer push tokens down — every window asks the runtime itself. */
export function acceptHostTokens(): () => void {
  return () => undefined
}

/** DEAD: there is nothing to renew in a window. The runtime renews, lazily, when asked. */
export function startAuthRenewal(): () => void {
  return () => undefined
}
