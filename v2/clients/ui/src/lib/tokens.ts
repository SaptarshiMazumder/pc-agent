/**
 * The token pair, and the refresh loop that keeps it alive.
 *
 * Access tokens live ~10 minutes; refresh tokens live 30 days and are single-use. That combination
 * is only safe if the client gets three things right, and each one has bitten real products:
 *
 *  1. SINGLE-FLIGHT REFRESH. Ten tabs waking from sleep together must produce ONE refresh, not
 *     ten. With rotating tokens, ten concurrent refreshes are ten uses of the same token, which
 *     the server correctly reads as theft and answers by revoking the whole family — signing the
 *     user out for being popular. Everything goes through one shared promise.
 *
 *  2. REFRESH BEFORE EXPIRY, not after. Waiting for a 401 means every tenth minute has a failed
 *     request in it. We renew at 80% of the token's life.
 *
 *  3. THE REFRESH TOKEN IS NEVER SENT ANYWHERE EXCEPT /auth/refresh. It never rides the socket
 *     URL and never reaches the model proxy. Only the short-lived access token travels.
 *
 * Storage is behind an interface so the desktop can put the refresh token in the OS keychain
 * (Electron safeStorage) while the web keeps it in localStorage. The ACCESS token is deliberately
 * memory-only in both: it is re-derivable from the refresh token, so writing it to disk adds a
 * place to steal it from and buys nothing.
 */

// lib/host.ts, NOT lib/platform.ts. platform.ts imports lib/auth.ts, which imports this module —
// so importing the adapter here would close a cycle (auth -> tokens -> platform -> auth) on the
// exact code path that runs first at boot. host.ts is the leaf that exists to prevent that; its
// header explains the trap in full.
import { hostSecrets, isDesktop } from './host'

export interface TokenPair {
  accessToken: string
  refreshToken: string
  /** Absolute epoch ms when the access token dies. */
  expiresAt: number
  accountId: string
  email: string
}

/** Where the long-lived half is kept. Desktop overrides this with an encrypted store. */
export interface RefreshStorage {
  read(): Promise<string | null>
  write(token: string | null): Promise<void>
}

const LS_REFRESH = 'agentd.refresh'

const localStorageRefresh: RefreshStorage = {
  async read() {
    try {
      return localStorage.getItem(LS_REFRESH)
    } catch {
      return null
    }
  },
  async write(token) {
    try {
      if (token) localStorage.setItem(LS_REFRESH, token)
      else localStorage.removeItem(LS_REFRESH)
    } catch {
      /* private mode — the in-memory copy still serves this session */
    }
  }
}

/**
 * Desktop: the OS-encrypted store, via the preload bridge. Falls back to localStorage when the
 * bridge is missing (an older shell) rather than losing sign-in entirely — a degraded store is
 * better than an app that cannot stay signed in.
 */
function pickStorage(): RefreshStorage {
  return hostSecrets() || localStorageRefresh
}

let storage: RefreshStorage = localStorageRefresh
let pair: TokenPair | null = null
let inflight: Promise<TokenPair | null> | null = null
let timer: ReturnType<typeof setTimeout> | null = null
const listeners = new Set<(p: TokenPair | null) => void>()

/**
 * Where to exchange a refresh token. A RESOLVER, not a snapshot.
 *
 * It held a copied string, set by whichever caller happened to run first — and one of them did
 * not: signing in fresh never called the configure step, so `authBase` stayed empty and every
 * later refresh returned null before making a request. The visible symptom would have been a
 * user signed out ten minutes after logging in, with nothing in the logs, and only for people who
 * signed in rather than resuming a stored session.
 *
 * A function cannot go stale and cannot be called too early: the address is read at the moment it
 * is needed, which also means discovery resolving later is picked up for free.
 */
let resolveAuthBase: () => string = () => ''

export function configureTokens(accountsUrl: string | (() => string)): void {
  resolveAuthBase = typeof accountsUrl === 'function'
    ? accountsUrl
    : () => (accountsUrl || '').replace(/\/$/, '')
  storage = pickStorage()
}

function authBaseNow(): string {
  return (resolveAuthBase() || '').replace(/\/$/, '')
}

export function onTokens(cb: (p: TokenPair | null) => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function announce(): void {
  listeners.forEach((l) => l(pair))
}

/** Seconds-to-ms, with the 80%-of-life renewal point. */
function renewDelay(expiresAt: number): number {
  const life = expiresAt - Date.now()
  // Never schedule in the past, and never busy-loop on a token that is already dead.
  return Math.max(5_000, Math.floor(life * 0.8))
}

function schedule(): void {
  if (timer) clearTimeout(timer)
  if (!pair) return
  timer = setTimeout(() => {
    void refresh()
  }, renewDelay(pair.expiresAt))
}

export function currentPair(): TokenPair | null {
  return pair
}

export async function setPair(next: TokenPair | null): Promise<void> {
  pair = next
  // PERSISTENCE MUST NEVER BLOCK SIGN-IN. The desktop writes through an IPC bridge to the OS
  // keychain, and any failure there — bridge missing, keychain unavailable, handler rejected —
  // used to propagate out of login() and land in the sign-in form as a failed login, AFTER the
  // server had already issued a perfectly good token. The user sees "please wait" and then the
  // login screen again, with the actual failure three layers away from anything they can see.
  //
  // Degrading is strictly better: the pair is already live in memory, so this session works
  // exactly as it should. The only thing lost is staying signed in across a restart, and that
  // is worth saying out loud rather than dying over.
  try {
    await storage.write(next?.refreshToken || null)
  } catch (e) {
    console.warn('[auth] could not persist the refresh token; staying signed in for this session only', e)
  }
  schedule()
  announce()
}

/** Adopt a server response from /auth/login, /auth/register or /auth/refresh. */
export function pairFromResponse(d: Record<string, unknown>): TokenPair {
  return {
    accessToken: String(d.access_token || ''),
    refreshToken: String(d.refresh_token || ''),
    // `expires_in` is RELATIVE on purpose (see identity/domain/token.py): our clock and the
    // server's may disagree, and a relative lifetime is correct under skew where an absolute
    // deadline is not.
    expiresAt: Date.now() + Number(d.expires_in || 600) * 1000,
    accountId: String(d.account_id || ''),
    email: String(d.email || '')
  }
}

/**
 * Trade the refresh token for a new pair. SINGLE-FLIGHT: concurrent callers share one request.
 *
 * Returns null when the session is genuinely over, and clears the stored token in that case —
 * a refresh token the server has rejected is never going to work, and keeping it means retrying
 * forever.
 */
export function refresh(): Promise<TokenPair | null> {
  if (inflight) return inflight
  inflight = (async () => {
    const token = pair?.refreshToken || (await storage.read())
    const base = authBaseNow()
    if (!token || !base) return null
    try {
      const r = await fetch(`${base}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token, client_id: clientId() })
      })
      if (!r.ok) {
        // 401 is terminal: expired, revoked, or the family was killed for reuse. Anything else
        // (network, 5xx) is transient and must NOT sign the user out — keep the token and let
        // the next attempt try again.
        if (r.status === 401 || r.status === 403) await setPair(null)
        return null
      }
      const next = pairFromResponse((await r.json()) as Record<string, unknown>)
      await setPair(next)
      return next
    } catch {
      return null // offline — keep the credential, retry later
    }
  })()
  try {
    return inflight
  } finally {
    void inflight.finally(() => {
      inflight = null
    })
  }
}

/**
 * A usable access token, refreshing first if the current one is spent.
 *
 * Everything that needs a credential goes through here, so there is exactly one place that knows
 * when to renew — and callers never have to reason about expiry.
 */
export async function getAccessToken(): Promise<string> {
  if (pair && pair.expiresAt - Date.now() > 30_000) return pair.accessToken
  const next = await refresh()
  return next?.accessToken || pair?.accessToken || ''
}

/** Restore a session from the stored refresh token (app start). */
export async function restore(): Promise<TokenPair | null> {
  const token = await storage.read()
  if (!token || !authBaseNow()) return null
  return refresh()
}

export async function clearTokens(): Promise<void> {
  if (timer) clearTimeout(timer)
  timer = null
  const token = pair?.refreshToken || (await storage.read())
  await setPair(null)
  // Best-effort server-side revocation. A sign-out that only forgets the token locally leaves a
  // 30-day credential alive on a machine the user may have just decided they do not trust.
  const base = authBaseNow()
  if (token && base) {
    try {
      await fetch(`${base}/auth/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token })
      })
    } catch {
      /* offline — the token still expires on its own */
    }
  }
}

function clientId(): string {
  return isDesktop ? 'desktop' : 'web'
}
