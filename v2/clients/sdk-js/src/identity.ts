/**
 * The agent window's TokenManager — ONE per storage key, shared by everything in the page.
 *
 * The SDK used to carry its own sign-in and its own renewal loop, a second implementation of what
 * `clients/ui` already did. They drifted, as two copies of one job do: this one refused to renew a
 * token that had already expired, had no single-flight guard, and posted to a different endpoint.
 * A user signed into an agent window was quietly signed out ten minutes later.
 *
 * So there is no implementation here at all — only the three facts `@agentd/auth` cannot know
 * about an agent window:
 *
 *   * WHERE THE ACCOUNTS SERVICE IS. The window asks its own daemon (`/platform/status`) rather
 *     than reading a build-time env, because an agent is served by whichever daemon happens to be
 *     running it.
 *   * WHERE THE SESSION LIVES. Keyed per agent, so two agent windows on one origin never share or
 *     clobber each other's — see `sessionKey`.
 *   * WHAT TO DO WHEN THE CREDENTIAL CHANGES. Swapped onto the OPEN socket with `auth.update`, so
 *     a renewal never interrupts a run in flight. Reconnecting instead would drop it, which is the
 *     one thing a silent background renewal must never do.
 *
 * NO REFRESH TOKEN IS A NORMAL STATE. A window opened by the desktop app is handed an access token
 * on its launch URL and never receives a refresh token — deliberately, since an agent is
 * third-party code and that is a 30-day credential for the whole account. Such a window cannot
 * renew and is fed instead (`adopt`, driven by `acceptHostTokens`).
 */

import { TokenManager, localSessionStore } from '@agentd/auth'
import type { AgentdClient } from './client'
import { type DaemonOptions, accountsUrl } from './platform-status'

export interface IdentityOptions extends DaemonOptions {
  /** The connected client, so a new credential can reach the daemon at once. */
  client?: AgentdClient
  /** Storage key override; defaults to one derived from the agent id in the page URL. */
  storageKey?: string
}

/**
 * The storage key for THIS window.
 *
 * `?scope=` is present only when an OPENER built the url (the desktop app, a launch link). A page
 * reached from a marketplace card is just `/apps/<id>/`, so the path is the only thing that says
 * which agent this is — and without that fallback every such app on one origin shares the key
 * `agentd.session.app`, i.e. one agent's session silently becomes another's.
 */
export function sessionKey(explicit = ''): string {
  if (explicit) return explicit
  const here = typeof location === 'undefined' ? null : new URL(location.href)
  const scope = here?.searchParams.get('scope') || ''
  const fromPath = /\/apps\/([^/]+)/.exec(here?.pathname || '')
  const id =
    /^agent:(.+)$/.exec(scope)?.[1] || (fromPath ? decodeURIComponent(fromPath[1]) : '')
  return `agentd.session.${id || 'app'}`
}

/**
 * One manager per key, for the life of the page.
 *
 * MEMOISED BECAUSE A SECOND INSTANCE IS A SECOND REFRESH. Refresh tokens are single-use and
 * rotating, and the server reads a reuse as theft — it revokes the whole family and signs the user
 * out everywhere. Two managers over one key would race exactly like two windows do, except
 * entirely within one page, and the single-flight guard inside a manager cannot see across
 * instances. So callers get the same object or none.
 */
const managers = new Map<string, TokenManager>()

export function identity(opts: IdentityOptions = {}): TokenManager {
  const key = sessionKey(opts.storageKey)
  const held = managers.get(key)
  if (held) {
    // The client arrives later than the first call in practice: `fromPage` reads the stored
    // session while building the socket, before anything has a client to hand over. Rebinding
    // keeps the ONE manager while letting the credential reach the socket once there is one.
    if (opts.client) bindClient(held, key, opts.client)
    return held
  }
  const manager = new TokenManager({
    accountsUrl: () => accountsUrl(opts),
    session: localSessionStore(key),
    // No `secrets`: a browser page has no OS keychain, so the refresh token — when this window
    // has one at all — rides in the same store. The desktop's answer to that is not to encrypt it
    // here but to never send one (see the header).
    clientId: 'app',
    deviceLabel: () => documentTitle() || 'Agent app',
    timeoutMs: opts.timeoutMs
  })
  managers.set(key, manager)
  if (opts.client) bindClient(manager, key, opts.client)
  manager.start()
  // SETTLE THE CREDENTIAL, ONCE, AT BOOT — and do not wait for it.
  //
  // Two windows need this and neither could ask for it. One that stored a session last time has a
  // spent access token and a live refresh token, and must trade up before anything uses it. One
  // opened by the desktop app arrives holding an access token and NO refresh token: it cannot
  // renew, so ten minutes later it goes anonymous, which the daemon does not refuse — the account's
  // agents just disappear from the window. `restore` covers both: it refreshes when it can, and
  // derives a session of its own when it cannot.
  //
  // Fire-and-forget because every caller here is synchronous (a socket URL is being built), and
  // because failing to settle leaves the window exactly as it was rather than stopping it.
  void manager.restore().catch(() => undefined)
  return manager
}

/** Which client a manager should push credentials at. One per key; the last caller wins. */
const bound = new Map<string, AgentdClient>()

function bindClient(manager: TokenManager, key: string, client: AgentdClient): void {
  const already = bound.get(key)
  bound.set(key, client)
  if (already === client) return
  if (already) return // subscribed once already; the map above redirects where it lands
  manager.subscribe((pair) => {
    const target = bound.get(key)
    if (!target) return
    if (!pair) {
      // Signed out. The daemon reads identity when the socket OPENS, so it keeps treating this
      // connection as the old account until the socket is rebuilt without the credential.
      target.reconnect()
      return
    }
    // Swap the credential on the OPEN socket rather than reconnecting: a reconnect drops an
    // in-flight run, which is exactly what a background renewal must not do. Falling back to a
    // reconnect covers a daemon too old to know `auth.update`.
    void target
      .request('auth.update', { accessToken: pair.accessToken })
      .catch(() => target.reconnect())
  })
}

function documentTitle(): string {
  try {
    return typeof document === 'undefined' ? '' : document.title
  } catch {
    return ''
  }
}

/** Drop the memoised managers. Tests only — a page has exactly one lifetime. */
export function resetIdentity(): void {
  managers.forEach((m) => m.stop())
  managers.clear()
  bound.clear()
}
