/**
 * Sign-in — IDENTITY, asked of the daemon.
 *
 * Three request/response calls over the socket the app already has:
 *
 *   auth.status   ->  can anyone sign in here, and is anyone signed in
 *   auth.login    ->  {email, password, signup?}
 *   auth.logout
 *
 * WHY THE DAEMON AND NOT THIS FILE. `platform.ts` next door does it the other way round: it asks
 * the daemon where the accounts service lives and then POSTs the user's password there from the
 * page. Two consequences followed, and both are the reason this module exists.
 *
 *   1. Every agent UI had to be TOLD the accounts address. The only channel that carried it was
 *      the daemon's distribution profile — a property of the packaged product — so an agent could
 *      not give itself a login without the whole build being reconfigured as a hosted flavour.
 *   2. Page JavaScript held a session token. These pages are served over plain HTTP by the
 *      daemon with no CSP, and a downloaded agent's UI is a stranger's code.
 *
 * Asking the daemon to perform the sign-in collapses both: the address and the token stay on the
 * other side of the socket, and the page learns only whether it worked.
 *
 * NOT ABOUT BILLING. `platform.ts` answers "are platform keys paying for model calls". This
 * answers "who is this". They were the same question, which is why a perfectly good sign-in on a
 * BYOK install used to be reported as a failure — nothing was paying, so nothing counted.
 */

import { AgentdClient, fromPage } from './client'

/** What `auth.status` reports. */
export interface AuthState {
  /** Is an accounts service configured at all? false => this daemon has no sign-in to offer,
   *  and a UI should not show a login. The ONE legitimate reason to hide the prompt. */
  available: boolean
  /** Is somebody signed in on this install right now? */
  signedIn: boolean
  /** Who — '' when signed out. */
  email: string
  accountId: string
}

export interface AuthOptions {
  /** Reuse an already-connected client. Omit and a short-lived one is opened for the call —
   *  which is what lets `mountSignInGate()` stay a zero-argument one-liner in every agent. */
  client?: AgentdClient
  /** How long to wait for a borrowed connection to come up. */
  timeoutMs?: number
}

const CONNECT_TIMEOUT_MS = 10000

/**
 * Run one request against the caller's client, or against a throwaway connection.
 *
 * The throwaway is closed in a `finally`, including when the request rejects — a wrong password
 * is the single most likely outcome here, and leaking a socket per attempt would be a slow leak
 * in exactly the loop a user retries.
 */
async function ask<T>(method: string, params: Record<string, unknown>, opts: AuthOptions): Promise<T> {
  if (opts.client) return opts.client.request<T>(method, params)

  const client = fromPage({ clientName: 'agentd-sdk-auth' })
  try {
    await opened(client, opts.timeoutMs ?? CONNECT_TIMEOUT_MS)
    return await client.request<T>(method, params)
  } finally {
    client.close()
  }
}

/** Resolve when the socket is open; reject if it does not come up in time. */
function opened(client: AgentdClient, timeoutMs: number): Promise<void> {
  if (client.connected) return Promise.resolve()
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      stop()
      reject(new Error(`the daemon did not answer within ${timeoutMs}ms`))
    }, timeoutMs)
    const stop = client.onStatus((status) => {
      if (status !== 'open') return
      clearTimeout(timer)
      stop()
      resolve()
    })
  })
}

function shape(raw: Record<string, any>): AuthState {
  return {
    available: !!raw?.available,
    signedIn: !!raw?.signedIn,
    email: String(raw?.email || ''),
    accountId: String(raw?.accountId || '')
  }
}

export async function authStatus(opts: AuthOptions = {}): Promise<AuthState> {
  return shape(await ask<Record<string, any>>('auth.status', {}, opts))
}

/**
 * Sign in, or create the account first when `signup`.
 *
 * REJECTS on a rejected credential, carrying the accounts service's own message ("incorrect
 * password") so a form has something to show. A failed attempt must never resolve to
 * `signedIn: false`: the caller cannot tell that apart from "signed out", and the user would be
 * shown a login form with no explanation of what just happened.
 */
export async function authLogin(
  args: { email: string; password: string; signup?: boolean },
  opts: AuthOptions = {}
): Promise<AuthState> {
  const raw = await ask<Record<string, any>>(
    'auth.login',
    { email: args.email, password: args.password, signup: !!args.signup },
    opts
  )
  return shape({ available: true, ...raw })
}

/** Forget the identity. Does NOT stop platform keys from paying — that is `platformDisconnect`. */
export async function authLogout(opts: AuthOptions = {}): Promise<AuthState> {
  const raw = await ask<Record<string, any>>('auth.logout', {}, opts)
  return shape({ available: true, ...raw })
}
