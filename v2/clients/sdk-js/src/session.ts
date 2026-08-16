/**
 * What THIS client knows about itself: who is signed in, and which keys it wants to pay with.
 *
 * A leaf — it imports nothing, so both the socket (client.ts) and the sign-in flow (auth.ts) can
 * read it without forming a cycle.
 *
 * THE CLIENT HOLDS BOTH FACTS. The daemon stores neither. That is not a stylistic choice: a
 * daemon-side session is ONE slot, and one slot cannot serve two people — the second to sign in
 * overwrites the first, signing out signs out everybody, and one window's Cloud switch moves
 * every other window's billing. Held per client and presented per connection, a hundred users on
 * one daemon is a hundred sockets with a hundred answers.
 *
 * Keyed per agent, so two agent apps on one machine never share or clobber each other's.
 */

export interface StoredSession {
  /** The ACCESS token — short-lived (~10 min) and the only one that travels on a connection. */
  token: string
  email: string
  accountId: string
  /**
   * The refresh token, used ONLY to mint a new access token at <accountsUrl>/auth/refresh.
   *
   * Without it an app window is signed in for exactly one access-token lifetime: the credential
   * rides the socket URL, the socket eventually reconnects, and the daemon then accepts the page
   * ANONYMOUSLY — which reads to the user as "all my agents disappeared", because an anonymous
   * connection sees none of the account's own agents.
   */
  refreshToken?: string
  /** Epoch ms when `token` expires, so renewal can happen BEFORE a request fails. */
  expiresAt?: number
}

/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
export type RunMode = 'local' | 'cloud'

function key(explicit = ''): string {
  if (explicit) return explicit
  const here = typeof location === 'undefined' ? null : new URL(location.href)
  const scope = here?.searchParams.get('scope') || ''
  // `?scope=` is present only when an OPENER built the url (the desktop shell, a launch link).
  // A page reached from a marketplace card is just `/apps/<id>/`, so the path is the only thing
  // that says which agent this is — and without that fallback every such app on one origin
  // shares the key `agentd.session.app`, i.e. one agent's session silently becomes another's.
  const id = /^agent:(.+)$/.exec(scope)?.[1] || pathAgentId(here)
  return `agentd.session.${id || 'app'}`
}

function pathAgentId(here: URL | null): string {
  const match = /\/apps\/([^/]+)/.exec(here?.pathname || '')
  return match ? decodeURIComponent(match[1]) : ''
}

/**
 * A credential this platform can still USE.
 *
 * Tokens are signed JWTs (three dot-separated parts). The opaque `sess_...` sessions that came
 * before them cannot be resolved by any current daemon, so a stored one is not a session — it is
 * a guarantee of failure. Keeping it looked harmless and was not: the page reported itself signed
 * in, presented the dead token on every connect, and the daemon refused each one, producing an
 * endless reconnect against our own server that no amount of retrying could ever fix.
 */
function usable(token: string): boolean {
  return !!token && !token.startsWith('sess_') && token.split('.').length === 3
}

/**
 * When an access token dies, in epoch ms — read from its own `exp` claim. 0 when unreadable.
 *
 * The claim is read, NOT trusted: nothing is authorised on the strength of it. The daemon verifies
 * the signature and would reject a token whose `exp` we misread in our favour. All this decides is
 * when the page should stop pretending the credential still works.
 */
export function accessTokenExpiry(token: string): number {
  try {
    const body = (token || '').split('.')[1]
    if (!body) return 0
    // base64url -> base64. atob is the one decoder present in every browser and in Node 16+.
    const json = atob(body.replace(/-/g, '+').replace(/_/g, '/'))
    const exp = Number(JSON.parse(json)?.exp || 0)
    return exp > 0 ? exp * 1000 : 0
  } catch {
    return 0 // not our token shape — `usable` already refuses those
  }
}

/** Renew slightly BEFORE the cliff, so the prompt arrives ahead of the first failed request. */
const EXPIRY_SKEW_MS = 30_000

export function loadSession(storageKey = ''): StoredSession | null {
  try {
    const raw = localStorage.getItem(key(storageKey))
    const parsed = raw ? (JSON.parse(raw) as StoredSession) : null
    if (!parsed || !parsed.token) return null
    if (!usable(parsed.token) || spent(parsed)) {
      // EVICT, do not merely ignore. Ignoring leaves it to be re-read on the next call and by
      // every other code path that looks at storage; removing it means the page shows a sign-in
      // form once and is then genuinely clean.
      localStorage.removeItem(key(storageKey))
      return null
    }
    return parsed
  } catch {
    return null // private mode / storage disabled — sign-in still works, it just won't persist
  }
}

/**
 * An access token that has run out AND has no refresh token behind it.
 *
 * WHY THIS COUNTS AS SIGNED OUT. An app window opened by the shell is handed its credential on the
 * launch url and holds no refresh token, so it cannot renew — see `fromPage` in client.ts. Ten
 * minutes later the token is dead, and the daemon does NOT refuse the reconnect: it accepts the
 * page ANONYMOUSLY. The page went on reporting itself signed in against a credential that had
 * expired, so the user saw their agents silently vanish with no error and no sign-in form — the
 * "logged out after ten minutes" report this exists to answer.
 *
 * Treating it as signed out turns that into one visible sign-in prompt, and signing in THERE
 * yields a real refresh token, so it does not recur for that window.
 *
 * WITH a refresh token, an expired access token is NOT spent: renewal is exactly the path that
 * fixes it (auth.ts `authRefresh`), and evicting here would sign out a session that was one HTTP
 * call from being fine.
 */
function spent(s: StoredSession): boolean {
  if (s.refreshToken) return false
  const expiresAt = s.expiresAt || accessTokenExpiry(s.token)
  return expiresAt > 0 && Date.now() > expiresAt - EXPIRY_SKEW_MS
}

export function saveSession(value: StoredSession | null, storageKey = ''): void {
  try {
    if (value) localStorage.setItem(key(storageKey), JSON.stringify(value))
    else localStorage.removeItem(key(storageKey))
  } catch {
    /* non-fatal */
  }
}

export function loadMode(storageKey = ''): RunMode | null {
  try {
    const v = localStorage.getItem(key(storageKey) + '.mode')
    return v === 'local' || v === 'cloud' ? v : null
  } catch {
    return null
  }
}

/** null clears the choice, returning this client to the default (cloud when it has a session). */
export function saveMode(value: RunMode | null, storageKey = ''): void {
  try {
    if (value) localStorage.setItem(key(storageKey) + '.mode', value)
    else localStorage.removeItem(key(storageKey) + '.mode')
  } catch {
    /* non-fatal */
  }
}

/**
 * The mode this client should run in: what it CHOSE, else the default.
 *
 * ONE PLACE, because two readers need the same answer and a disagreement between them is
 * invisible: the settings page renders it, and the socket sends it. If the page defaulted to
 * cloud while the connect URL sent nothing, the UI would promise platform keys while the calls
 * went out on the user's own.
 *
 * Default is CLOUD once signed in — and only where there is a proxy to reach.
 */
export function effectiveMode(storageKey = '', signedIn = false, canUseCloud = true): RunMode {
  const chosen = loadMode(storageKey)
  if (chosen) return chosen
  return signedIn && canUseCloud ? 'cloud' : 'local'
}
