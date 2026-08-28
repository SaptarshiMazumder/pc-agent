/**
 * The WEB client's TokenManager — the browser deployment's whole session machinery.
 *
 * THE DESKTOP DOES NOT RUN THIS. There the runtime holds the machine's one session
 * (agent_runtime/infrastructure/platform_session.py) and lib/auth.ts asks it over local HTTP;
 * `configureTokens` is never called, so nothing in this file executes. On the WEB there is no
 * runtime to ask, so the manager remains — in COOKIE MODE: the refresh token lives in an
 * HttpOnly cookie at the accounts service, nothing durable is stored in the page, and the
 * single-flight refresh (@agentd/auth) keeps the ten-minute access token alive in memory.
 */

// lib/host.ts, NOT lib/platform.ts. platform.ts imports lib/auth.ts, which imports this module —
// so importing the adapter here would close a cycle (auth -> tokens -> platform -> auth) on the
// exact code path that runs first at boot. host.ts is the leaf that exists to prevent that; its
// header explains the trap in full.
import { TokenManager, memorySessionStore } from '@agentd/auth'
import type { TokenPair } from '@agentd/auth'
import { hostOs } from './host'

export type { TokenPair } from '@agentd/auth'

/** Where the long-lived half is kept. Desktop overrides this with an encrypted store. */
export interface RefreshStorage {
  read(): Promise<string | null>
  write(token: string | null): Promise<void>
}

/**
 * The non-secret half of the session.
 *
 * SEPARATE FROM THE KEY `lib/auth.ts` USED TO WRITE. That one held a hand-maintained copy of the
 * same three fields, updated by whoever remembered to; this is written by the manager itself, so
 * the rendered account and the credential in use cannot disagree.
 */
const LS_SESSION = 'agentd.auth'

const LS_REFRESH = 'agentd.refresh'


/**
 * WEB ONLY, and transitional: the refresh token this browser stored BEFORE cookie mode existed.
 *
 * Read once so the first boot-time refresh can spend it — the server rotates it straight into
 * the HttpOnly cookie, signing the user in with no visible seam — and cleared on every write,
 * because the web never stores a credential again. A browser with nothing stored reads null and
 * the cookie (or a sign-in form) answers instead. Delete this store once deployed installs have
 * all rotated.
 */
const legacyWebRefresh: RefreshStorage = {
  async read() {
    try {
      return localStorage.getItem(LS_REFRESH)
    } catch {
      return null
    }
  },
  async write() {
    try {
      localStorage.removeItem(LS_REFRESH)
      localStorage.removeItem(LS_SESSION)
    } catch {
      /* nothing stored, nothing to clear */
    }
  }
}

/**
 * Where to exchange a refresh token. A RESOLVER, not a snapshot.
 *
 * It held a copied string, set by whichever caller happened to run first — and one of them did
 * not: signing in fresh never called the configure step, so `authBase` stayed empty and every
 * later refresh returned null before making a request. The visible symptom would have been a user
 * signed out ten minutes after logging in, with nothing in the logs, and only for people who
 * signed in rather than resuming a stored session.
 *
 * A function cannot go stale and cannot be called too early: the address is read at the moment it
 * is needed, which also means discovery resolving later is picked up for free.
 */
let resolveAuthBase: () => string = () => ''

let manager: TokenManager | null = null
const listeners = new Set<(p: TokenPair | null) => void>()

export function configureTokens(accountsUrl: string | (() => string)): void {
  resolveAuthBase =
    typeof accountsUrl === 'function'
      ? accountsUrl
      : () => (accountsUrl || '').replace(/\/$/, '')
  // Rebuilt rather than mutated, so a host that reconfigures gets a manager whose storage matches
  // what it just asked for. Any prior renewal timer is stopped with it.
  manager?.stop()
  // THE WEB HOLDS NOTHING. Its session is an HttpOnly cookie at the accounts service
  // (identity/presentation/auth_router.py): the session store is memory, the secrets store
  // exists only to retire a pre-cookie refresh token, and `cookies` makes every auth request
  // carry the jar. The desktop keeps its stores until it converges on the runtime (phase 4).
  manager = new TokenManager({
    accountsUrl: () => (resolveAuthBase() || '').replace(/\/$/, ''),
    session: memorySessionStore(),
    secrets: legacyWebRefresh,
    cookies: true,
    clientId: 'web',
    deviceLabel: () => `Web · ${hostOs() || 'unknown'}`,
    onChange: (pair) => listeners.forEach((l) => l(pair))
  })
  manager.start()
}

/**
 * The manager, once configured.
 *
 * THROWS rather than lazily building one, because a manager built without an accounts URL is a
 * manager that silently never refreshes — the exact failure the resolver above exists to prevent.
 * Reaching here before `configureTokens` is a wiring bug and should read as one.
 */
export function tokens(): TokenManager {
  if (!manager) throw new Error('configureTokens() has not run — no accounts service configured')
  return manager
}

export function onTokens(cb: (p: TokenPair | null) => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

export function currentPair(): TokenPair | null {
  return manager?.current() || null
}

/** Trade the refresh token for a new pair. Single-flight; see `@agentd/auth`. */
export function refresh(): Promise<TokenPair | null> {
  return manager ? manager.refresh() : Promise.resolve(null)
}

/**
 * A usable access token, refreshing first if the current one is spent.
 *
 * Everything that needs a credential goes through here, so there is exactly one place that knows
 * when to renew — and callers never have to reason about expiry.
 */
export function getAccessToken(): Promise<string> {
  return manager ? manager.accessToken() : Promise.resolve('')
}

/** Restore a session from the stored refresh token (app start). */
export function restore(): Promise<TokenPair | null> {
  return manager ? manager.restore() : Promise.resolve(null)
}

export async function clearTokens(): Promise<void> {
  await manager?.logout()
}
