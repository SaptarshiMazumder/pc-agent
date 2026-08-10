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
  token: string
  email: string
  accountId: string
}

/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
export type RunMode = 'local' | 'cloud'

function key(explicit = ''): string {
  if (explicit) return explicit
  const here = typeof location === 'undefined' ? null : new URL(location.href)
  const scope = here?.searchParams.get('scope') || ''
  const id = /^agent:(.+)$/.exec(scope)?.[1] || ''
  return `agentd.session.${id || 'app'}`
}

export function loadSession(storageKey = ''): StoredSession | null {
  try {
    const raw = localStorage.getItem(key(storageKey))
    const parsed = raw ? (JSON.parse(raw) as StoredSession) : null
    return parsed && parsed.token ? parsed : null
  } catch {
    return null // private mode / storage disabled — sign-in still works, it just won't persist
  }
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
