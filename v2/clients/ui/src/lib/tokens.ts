/**
 * This client's TokenManager, and the three facts `@agentd/auth` cannot know about it.
 *
 * THE IMPLEMENTATION MOVED, THE BEHAVIOUR DID NOT. Single-flight refresh, renewing at 80% of a
 * token's life rather than after a 401, and never sending the refresh token anywhere but
 * `/auth/refresh` — all of it still holds, and all of it now lives in `@agentd/auth` so that the
 * agent SDK runs the same code instead of its own worse copy of it. What is left here is what is
 * genuinely local: where the accounts service is, where a secret goes on THIS host, and what to do
 * when the credential changes.
 *
 * THE ACCESS TOKEN IS NOT KEPT IN THE OS KEYCHAIN — only the refresh token is. It is re-derivable
 * from that one, so a second copy on disk adds a place to steal it from and buys nothing.
 */

// lib/host.ts, NOT lib/platform.ts. platform.ts imports lib/auth.ts, which imports this module —
// so importing the adapter here would close a cycle (auth -> tokens -> platform -> auth) on the
// exact code path that runs first at boot. host.ts is the leaf that exists to prevent that; its
// header explains the trap in full.
import { TokenManager, localSessionStore } from '@agentd/auth'
import type { TokenPair } from '@agentd/auth'
import { hostOs, hostSecrets, isDesktop } from './host'

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
  manager = new TokenManager({
    accountsUrl: () => (resolveAuthBase() || '').replace(/\/$/, ''),
    session: localSessionStore(LS_SESSION),
    secrets: pickStorage(),
    clientId: isDesktop ? 'desktop' : 'web',
    deviceLabel: () => `${isDesktop ? 'Desktop' : 'Web'} · ${hostOs() || 'unknown'}`,
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
