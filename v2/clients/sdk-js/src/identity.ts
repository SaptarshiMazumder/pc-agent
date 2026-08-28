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
import { daemonOrigin, daemonToken, type DaemonOptions } from './platform-status'

export interface IdentityOptions extends DaemonOptions {
  /** Accepted for compatibility; the fetcher needs no client. */
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

/** Ask the runtime for the machine's token state. The ONE identity read everything builds on. */
export async function fetchToken(opts: DaemonOptions = {}): Promise<TokenAnswer> {
  try {
    const r = await fetch(authUrl('/auth/token', opts), {
      cache: 'no-store',
    })
    const d = (await r.json().catch(() => ({}))) as TokenAnswer
    if (d && typeof d.state === 'string') return d
    return { state: r.ok ? 'ok' : 'signed_out' }
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

  constructor(private readonly opts: DaemonOptions) {}

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
        this.answer = a
      })
    }
    return this.inflight
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
