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
 * IDENTITY IS THE MACHINE'S. The runtime holds the one session and every window reads the same
 * fact, so signing in or out ANYWHERE moves every window at once — this hook hears about it on
 * the socket (`auth.changed`) and re-reads. One account per machine; the web deployment is where
 * simultaneous accounts live.
 *
 * IT READS OVER HTTP, NOT THE SOCKET. Sign-in state is answerable before the socket is up — and it
 * is frequently the EXPLANATION for why the socket is not up. A hook that waited for a connection
 * to tell you that you are not signed in would be silent exactly when it is needed.
 */

import {
  authLogout,
  authStatus,
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
  /** Ask for the sign-in card. It is a COMPONENT now, so this only raises the flag below —
   *  the app renders `<SignIn>` while it is set. The old gate built its own DOM over the page,
   *  which is why this used to be something you awaited. */
  signIn: () => void
  /** Is the card being asked for? Render `<SignIn onDone={signedIn}>` while true. */
  wantsSignIn: boolean
  /** Call from the card's `onDone`: lowers the flag and re-reads the account. */
  signedIn: () => void
  signOut: () => Promise<void>
  chooseMode: (mode: RunMode) => Promise<void>
  reload: () => void
}

/* NO `product` PARAMETER. It used to feed `mountSignInGate({product})`, the vanilla gate that is
 * gone; the name on the card now comes from whoever renders `<SignIn>`. */
export function useAuth(client: AgentdClient): Auth {
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

  useEffect(() => {
    load()
    // Sign-in state is MACHINE state: the runtime broadcasts `auth.changed` to every window
    // when any of them signs in or out. Without this, a second window renders the old account
    // until something makes it ask again.
    return client.on('auth.changed', () => load())
  }, [load, client])

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

  const [wantsSignIn, setWantsSignIn] = useState(false)
  const signIn = useCallback(() => setWantsSignIn(true), [])
  const signedIn = useCallback(() => {
    setWantsSignIn(false)
    void load()
  }, [load])

  const signOut = useCallback(() => run(() => authLogout({ client })), [run, client])

  /** Local (the user's own API keys) or Cloud (platform keys, metered to their account).
   *  MACHINE-WIDE, unlike identity: the model proxy is one piece of daemon state shared by every
   *  agent, so this moves the others too. Say so wherever you render it. */
  const chooseMode = useCallback(
    (mode: RunMode) => run(() => setRunMode(mode, { client })),
    [run, client],
  )

  return { auth, busy, error, signIn, wantsSignIn, signedIn, signOut, chooseMode, reload: load }
}
