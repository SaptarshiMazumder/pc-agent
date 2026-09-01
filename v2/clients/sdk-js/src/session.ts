/**
 * What THIS client knows about itself: which keys it wants to pay with, and (hosted only) the
 * borrowed token an opener handed it on the launch URL.
 *
 * THE CREDENTIAL STORY LEFT THIS FILE. On desktop the runtime is the only session holder
 * (platform_session.py); a window asks `GET /auth/token` and stores NOTHING — see identity.ts.
 * What remains here is genuinely per-window:
 *
 *   * RUN MODE — which keys pay for this client's model calls. A preference, not a credential,
 *     so localStorage is exactly right for it.
 *   * THE PAGE SESSION — on a HOSTED daemon there is no machine session to inherit, so a window
 *     opened by another app still adopts the access token from its launch URL. It now lives in
 *     module memory for the life of the page, never in storage: this window runs third-party
 *     code and must not persist a credential, and there is nothing that could renew it anyway.
 */

import { accessTokenAccount, accessTokenEmail, accessTokenExpiry } from '@agentd/auth'
import { sessionKey } from './identity'

export { accessTokenAccount, accessTokenExpiry } from '@agentd/auth'

/**
 * A session as the rest of the SDK reads it.
 *
 * Kept in this shape because agent apps already destructure it. Only the hosted launch-URL path
 * produces one now; on desktop `loadSession` answers null and identity comes from the runtime.
 */
export interface StoredSession {
  /** The ACCESS token — short-lived and the only one that travels on a connection. */
  token: string
  email: string
  accountId: string
  /** Never present any more — windows do not hold refresh tokens. Kept for destructurers. */
  refreshToken?: string
  /** Epoch ms when `token` expires. */
  expiresAt?: number
}

/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
export type RunMode = 'local' | 'cloud'

/** The page's borrowed session, if an opener supplied one. MEMORY, not storage — gone on
 *  reload, which is correct: the opener re-stamps the URL on every launch. */
let pageSession: StoredSession | null = null

/**
 * The session this page was HANDED, or null.
 *
 * SYNCHRONOUS, because the socket URL is built from it. Null on every desktop window — there
 * the daemon inherits the machine's identity for the connection and nothing travels at all.
 * Anything that needs a credential it can RELY on should await `identity().accessToken()`.
 */
export function loadSession(_storageKey = ''): StoredSession | null {
  const s = pageSession
  if (!s) return null
  // A dead borrowed token is worse than none: presenting it makes the daemon accept the
  // reconnect ANONYMOUSLY, signed in by its own account, invisible to the user.
  if (s.expiresAt && s.expiresAt <= Date.now()) return null
  return s
}

/**
 * Adopt a launch-URL session (or clear it with null).
 *
 * The ONE legitimate caller is `fromPage` in client.ts. Everything else signs in through the
 * runtime (`authLogin`) and stores nothing here.
 */
export function saveSession(value: StoredSession | null, _storageKey = ''): void {
  if (!value) {
    pageSession = null
    return
  }
  pageSession = {
    token: value.token,
    // The token's own claims fill what the opener did not say — a launch URL carries the token
    // and nothing else, and these blanks are what made opened windows render "Account" unnamed.
    email: value.email || accessTokenEmail(value.token),
    accountId: value.accountId || accessTokenAccount(value.token),
    expiresAt: value.expiresAt || accessTokenExpiry(value.token) || undefined
  }
}

export function loadMode(storageKey = ''): RunMode | null {
  try {
    const v = localStorage.getItem(sessionKey(storageKey) + '.mode')
    return v === 'local' || v === 'cloud' ? v : null
  } catch {
    return null
  }
}

/** null clears the choice, returning this client to the default (cloud when it has a session). */
export function saveMode(value: RunMode | null, storageKey = ''): void {
  try {
    if (value) localStorage.setItem(sessionKey(storageKey) + '.mode', value)
    else localStorage.removeItem(sessionKey(storageKey) + '.mode')
  } catch {
    /* non-fatal */
  }
}

/**
 * The mode this client should run in: what it CHOSE, else the default.
 *
 * ONE PLACE, because two readers need the same answer and a disagreement between them is
 * invisible: the settings page renders it, and the socket sends it. If the page defaulted to cloud
 * while the connect URL sent nothing, the UI would promise platform keys while the calls went out
 * on the user's own.
 *
 * Default is CLOUD once signed in — and only where there is a proxy to reach.
 */
export function effectiveMode(storageKey = '', signedIn = false, canUseCloud = true): RunMode {
  const chosen = loadMode(storageKey)
  if (chosen) return chosen
  return signedIn && canUseCloud ? 'cloud' : 'local'
}
