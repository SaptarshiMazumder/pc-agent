/* useAuth — who is using this window, and the two buttons that change it.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. If you need something it does not expose, add it there so every agent gets it.
 *
 * THE SDK OWNS THE MECHANISM. Signing in, storing the token, renewing it before it dies, telling
 * the daemon: all of that is `@agentd/client`. This file is React state around it, nothing more.
 * That split is why there is one implementation of credentials in the product instead of one per
 * agent — and credentials are the thing you least want written twice.
 *
 * IDENTITY IS PER WINDOW. Each window keeps its own session, so two windows can be two different
 * people at once, and signing out here signs out nothing else. That is deliberate: one machine,
 * many accounts.
 *
 * IT READS OVER HTTP, NOT THE SOCKET. Sign-in state is answerable before the socket is up — and it
 * is frequently the EXPLANATION for why the socket is not up. A hook that waited for a connection
 * to tell you that you are not signed in would be silent exactly when it is needed.
 */

import {
  authLogout,
  authStatus,
  mountSignInGate,
  setRunMode,
  type AgentdClient,
  type AuthState,
  type RunMode,
} from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'

export type { AuthState, RunMode }

export interface Auth {
  /** Null until the first read completes, or when it failed — see `error`. */
  auth: AuthState | null
  busy: boolean
  /** Why the account could not be read. Shown, never swallowed: an account control that quietly
   *  does nothing is indistinguishable from a build that has no accounts service at all. */
  error: string
  signIn: () => Promise<void>
  signOut: () => Promise<void>
  chooseMode: (mode: RunMode) => Promise<void>
  reload: () => void
}

export function useAuth(client: AgentdClient, product = ''): Auth {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    void (async () => {
      try {
        setAuth(await authStatus({ client }))
        setError('')
      } catch (e) {
        setError(String((e as Error)?.message || e))
      }
    })()
  }, [client])

  useEffect(() => load(), [load])

  /** One runner for every action, so none of them can forget to clear `busy` or to REPORT.
   *  A rejected sign-in that resolves silently leaves a button that does nothing and says
   *  nothing, which is the single most confusing thing an account control can do. */
  const run = useCallback(async (fn: () => Promise<AuthState>) => {
    setBusy(true)
    setError('')
    try {
      setAuth(await fn())
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }, [])

  const signIn = useCallback(
    () =>
      run(() =>
        mountSignInGate({
          client,
          // Falls back to the page title, so an agent that passes nothing still names itself.
          product: product || undefined,
          blurb: 'Sign in to use your account in this window.',
        }),
      ),
    [run, client, product],
  )

  const signOut = useCallback(() => run(() => authLogout({ client })), [run, client])

  /** Local (the user's own API keys) or Cloud (platform keys, metered to their account).
   *  MACHINE-WIDE, unlike identity: the model proxy is one piece of daemon state shared by every
   *  agent, so this moves the others too. Say so wherever you render it. */
  const chooseMode = useCallback(
    (mode: RunMode) => run(() => setRunMode(mode, { client })),
    [run, client],
  )

  return { auth, busy, error, signIn, signOut, chooseMode, reload: load }
}
