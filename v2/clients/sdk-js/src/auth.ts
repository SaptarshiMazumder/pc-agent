/**
 * Sign-in for an agent window — ORDINARY HTTP, from the client, exactly like any web app.
 *
 *   GET  <daemon>/platform/status   → where the accounts service is
 *   POST <accounts>/signup          (only when creating)
 *   POST <accounts>/auth/login      → an access token and a refresh token
 *
 * The daemon is not in the middle of this. It answers one question — "where do people sign in?" —
 * and is then told the answer on the next connection.
 *
 * WHY NOT THROUGH THE DAEMON. It was, briefly: three socket methods, with the daemon performing
 * the exchange and keeping the token. That put ONE session on the machine, and one session cannot
 * serve two people — the second to sign in overwrote the first, signing out signed out everybody,
 * and any way to read the token back handed one user another's credential.
 *
 * EVERY LINE OF CREDENTIAL HANDLING BELOW IS A DELEGATION. This file used to implement sign-in,
 * refresh and a renewal timer itself — a second copy of what `clients/ui` already had, which
 * drifted from it and lost. It would not renew a token that had ALREADY expired (the one case that
 * matters), it had no single-flight guard, so two windows waking together could trip the server's
 * refresh-reuse detector and get the whole family revoked, and it posted to `/login` where the
 * other client posted to `/auth/login`. One implementation now lives in `@agentd/auth` and both
 * clients call it. Adding credential logic here means writing the third copy, so do not.
 */

import type { AgentdClient } from './client'
import { identity } from './identity'
import { type DaemonOptions, platformStatus } from './platform-status'
import { type RunMode, effectiveMode, loadMode, saveMode } from './session'

export interface AuthState {
  /** Is an accounts service configured on this daemon? false => no sign-in to offer. */
  available: boolean
  /** Is THIS client signed in? */
  signedIn: boolean
  email: string
  accountId: string
  /** Which keys this client's model calls run on. */
  mode: RunMode
  /** Is there a Cloud to switch to on this build? */
  canUseCloud: boolean
  /**
   * Does this daemon DEMAND an account, or merely offer one?
   *
   * `available` says an accounts service exists. That is not the same question, and conflating
   * them is why a desktop daemon — which accepts the machine token and requires no account at
   * all — still put a sign-in form in front of every window. Only the daemon knows: it is an
   * explicit hosted opt-in, not something a client can infer from a configured URL.
   */
  required: boolean
}

export interface AuthOptions extends DaemonOptions {
  /** The connected client, so a change can reach the daemon at once. */
  client?: AgentdClient
  /** Storage key override; defaults to one derived from the agent id in the page URL. */
  storageKey?: string
}

/** What this client is, right now: its own state, plus what the daemon offers. */
export async function authStatus(opts: AuthOptions = {}): Promise<AuthState> {
  const status = await platformStatus(opts)
  const manager = identity(opts)
  const signedIn = manager.signedIn()
  const held = manager.current()
  const canUseCloud = !!status.canUseCloud
  return {
    available: !!String(status.accountsUrl || ''),
    signedIn,
    email: (signedIn && held?.email) || '',
    accountId: (signedIn && held?.accountId) || '',
    mode: effectiveMode(opts.storageKey, signedIn, canUseCloud),
    canUseCloud,
    // Absent on an older daemon. Defaulting to TRUE keeps the gate exactly as it was there — a
    // client that guessed "not required" against a daemon that requires it would show no login and
    // then fail every call with no explanation.
    required: status.signInRequired !== false
  }
}

/**
 * Sign in, or create the account first when `signup`.
 *
 * REJECTS on a rejected credential, carrying the accounts service's own message ("incorrect
 * password") so a form has something to show. A failed attempt must never resolve to
 * `signedIn: false`: the caller cannot tell that apart from "signed out", and the user is left
 * looking at a form that cleared itself.
 */
export async function authLogin(
  args: { email: string; password: string; signup?: boolean },
  opts: AuthOptions = {}
): Promise<AuthState> {
  await identity(opts).login(args)
  return authStatus(opts)
}

/**
 * Renew the access token now. Returns the new one, or '' when there was nothing to renew with.
 *
 * Rarely wanted directly: renewal runs on its own from the moment this window has a session, and
 * anything that needs a credential should ASK for one (`identity().accessToken()`) rather than
 * renew first and hope.
 */
export async function authRefresh(opts: AuthOptions = {}): Promise<string> {
  const next = await identity(opts).refresh()
  return next?.accessToken || ''
}

/**
 * Keep this window's access token fresh for as long as the page is open. Returns a stop function.
 *
 * Renewal is ALREADY RUNNING by the time anything can call this — it starts with the manager, so a
 * window cannot end up holding a credential nothing is looking after just because its author did
 * not know to ask. Kept because agent apps call it, and because saying so explicitly is
 * reasonable. Idempotent.
 */
export function startAuthRenewal(opts: AuthOptions = {}): () => void {
  return identity(opts).start()
}

/**
 * Accept access tokens pushed down by the desktop app. Returns an unsubscribe.
 *
 * A window opened from the desktop app gets its credential on the launch URL and holds NO refresh
 * token — deliberately, because an agent app is third-party code and a refresh token is a 30-day
 * credential for the user's whole account. So it cannot renew itself; the desktop app, which does
 * hold the refresh token, mints short-lived access tokens and hands them down (see the desktop's
 * src/preload/app.ts). A no-op in a browser tab, where there is no bridge to listen to.
 *
 * WHETHER a pushed token is taken is the manager's decision (`adopt`): the push reaches every open
 * window at once, and a window signed in as somebody else must not silently become the pusher's
 * account.
 */
export function acceptHostTokens(opts: AuthOptions = {}): () => void {
  const host = (
    globalThis as { agentdHost?: { onAccessToken(cb: (t: string) => void): () => void } }
  ).agentdHost
  if (!host?.onAccessToken) return () => undefined
  const manager = identity(opts)
  return host.onAccessToken((token) => {
    if (token) void manager.adopt(token)
  })
}

/** Forget this client's session. Other windows keep theirs — each holds its own. */
export async function authLogout(opts: AuthOptions = {}): Promise<AuthState> {
  await identity(opts).logout()
  saveMode(null, opts.storageKey)
  return authStatus(opts)
}

/** Choose which keys pay for THIS client's model calls. Other clients are unaffected. */
export async function setRunMode(mode: RunMode, opts: AuthOptions = {}): Promise<AuthState> {
  if (mode === 'cloud' && !identity(opts).signedIn()) {
    throw new Error('sign in first — Cloud mode meters model calls to your account')
  }
  saveMode(mode, opts.storageKey)
  opts.client?.reconnect() // the daemon reads the mode when the socket opens
  return authStatus(opts)
}

/** Re-exported so a caller that only imports auth.ts can still read the stored choice. */
export { loadMode }
