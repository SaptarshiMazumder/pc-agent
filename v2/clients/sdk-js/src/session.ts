/**
 * What THIS client knows about itself: who is signed in, and which keys it wants to pay with.
 *
 * THE CLIENT HOLDS BOTH FACTS. The daemon stores neither. That is not a stylistic choice: a
 * daemon-side session is ONE slot, and one slot cannot serve two people — the second to sign in
 * overwrites the first, signing out signs out everybody, and one window's Cloud switch moves every
 * other window's billing. Held per client and presented per connection, a hundred users on one
 * daemon is a hundred sockets with a hundred answers.
 *
 * THE CREDENTIAL HALF NOW LIVES IN `@agentd/auth`, not here. This module used to own the storage
 * format, the expiry rules and (next door, in auth.ts) a renewal loop — a second implementation of
 * what the agentd client already did, which drifted from it and lost. What is left here is the
 * part that genuinely is this client's own: WHICH KEYS PAY, which is not an identity question and
 * has no server-side equivalent.
 */

import { accessTokenAccount, accessTokenEmail, accessTokenExpiry } from '@agentd/auth'
import { identity, sessionKey } from './identity'

export { accessTokenAccount, accessTokenExpiry } from '@agentd/auth'

/**
 * A session as the rest of the SDK reads it.
 *
 * A projection of `@agentd/auth`'s `TokenPair`, kept in this shape because agent apps already
 * destructure it. The manager is the source of truth; this is a view of it.
 */
export interface StoredSession {
  /** The ACCESS token — short-lived and the only one that travels on a connection. */
  token: string
  email: string
  accountId: string
  /** Absent in a window opened BY the desktop app: it is fed tokens instead of renewing. */
  refreshToken?: string
  /** Epoch ms when `token` expires. */
  expiresAt?: number
}

/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
export type RunMode = 'local' | 'cloud'

/**
 * The session this client can still use, or null.
 *
 * SYNCHRONOUS, because the socket URL is built from it and a page must be able to answer "who am
 * I" before its first await. Renewal happens on its own schedule; this reports what is held now.
 * Anything that needs a credential it can RELY on should await `identity().accessToken()`, which
 * renews first.
 */
export function loadSession(storageKey = ''): StoredSession | null {
  const manager = identity({ storageKey })
  if (!manager.signedIn()) return null
  const p = manager.current()
  if (!p) return null
  return {
    token: p.accessToken,
    email: p.email,
    accountId: p.accountId,
    refreshToken: p.refreshToken || undefined,
    expiresAt: p.expiresAt || undefined
  }
}

/**
 * Write a session directly.
 *
 * The ONE legitimate caller is `fromPage` in client.ts, adopting the access token an opener put on
 * the launch URL. Everything else should go through sign-in or renewal — writing a credential by
 * hand is how a page ends up holding one nothing can renew.
 */
export function saveSession(value: StoredSession | null, storageKey = ''): void {
  const manager = identity({ storageKey })
  if (!value) {
    // Forgets, and does NOT revoke. Clearing local state is all this has ever meant, and it has to
    // stay synchronous — the caller may be about to rebuild a socket. Telling the server is
    // `authLogout`, which is a different intention with a different cost.
    manager.replace(null)
    return
  }
  manager.replace({
    accessToken: value.token,
    refreshToken: value.refreshToken || '',
    expiresAt: value.expiresAt || accessTokenExpiry(value.token),
    // The token's own claims fill what the opener did not say. A launch URL carries the token
    // and nothing else, and these two blanks are what made every opened window render its account
    // menu as "Account" with no name.
    accountId: value.accountId || accessTokenAccount(value.token),
    email: value.email || accessTokenEmail(value.token)
  })
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
