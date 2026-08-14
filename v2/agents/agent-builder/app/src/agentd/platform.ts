/* WHO you are, and who PAYS. Two facts, two sources, and the difference matters.
 *
 *   platform.status   what THIS CONNECTION carries. Asked of the daemon, per socket.
 *   authStatus()      what this CLIENT stores: its session and its chosen run mode.
 *
 * They can disagree — after a reconnect, or when the shell signed in but this window's socket did
 * not — and the identity chip in the topbar uses the connection's answer on purpose: it names the
 * identity a Publish would be signed with, and a stored session that the socket is not using
 * would name the wrong publisher.
 */

import {
  authLogout,
  authStatus,
  loadMode,
  mountSignInGate,
  setRunMode,
  type AgentdClient,
  type AuthState,
  type RunMode,
} from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'
import { useDaemonEvent } from './client'

export interface WhoAmI {
  /** false when the daemon is too old to answer — say nothing rather than something wrong. */
  known: boolean
  signedIn: boolean
  label: string
  title: string
}

/** The identity chip. Re-asked on EVERY socket open, not just the first: signing in re-dials the
 *  socket with the new session, and a chip that describes a connection must follow it. */
export function useWhoAmI(client: AgentdClient, status: string): WhoAmI {
  const [who, setWho] = useState<WhoAmI>({ known: false, signedIn: false, label: '', title: '' })

  useEffect(() => {
    if (status !== 'open') return
    let live = true
    void (async () => {
      try {
        const s: any = await client.request('platform.status')
        if (!live) return
        setWho(
          s?.signedIn
            ? {
                known: true,
                signedIn: true,
                label: String(s.email || s.accountId || ''),
                title:
                  `Signed in — publishes are attributed to ${s.email || s.accountId}` +
                  (s.mode ? ` (${s.mode} mode)` : ''),
              }
            : {
                known: true,
                signedIn: false,
                label: 'not signed in',
                title: 'This window has no account — publishing will ask you to sign in.',
              },
        )
      } catch {
        // Daemon predates platform.status on app sockets. Hidden entirely: "not signed in" would
        // be a claim we cannot support.
        if (live) setWho({ known: false, signedIn: false, label: '', title: '' })
      }
    })()
    return () => {
      live = false
    }
  }, [client, status])

  return who
}

export interface PlatformState {
  auth: AuthState | null
  /** The mode the user CHOSE, as opposed to the one in force. '' means they never chose.
   *
   *  "Cloud" and "Cloud because nobody said otherwise" are different states, and only the second
   *  one changes by itself when someone signs in — so the page has to be able to tell them apart. */
  chosen: RunMode | ''
  error: string
}

/** Identity and run mode for the Settings page, and the three actions that change them. */
export function usePlatform(client: AgentdClient) {
  const [state, setState] = useState<PlatformState>({ auth: null, chosen: '', error: '' })

  const reload = useCallback(async () => {
    try {
      // ONE call, and it is the SDK's: identity and run mode are this client's own state, so
      // there is nothing to ask the daemon for beyond "is there an accounts service, and is there
      // a proxy to switch to". Both used to be daemon methods, which made them machine-wide.
      const auth = await authStatus({ client })
      setState({ auth, chosen: loadMode() || '', error: '' })
    } catch (e) {
      // Remembered rather than swallowed: a Run mode control that quietly vanishes looks
      // identical to a build that has no Cloud.
      setState({ auth: null, chosen: '', error: String((e as Error)?.message || e) })
    }
  }, [client])

  // The daemon pushes this whenever identity or run mode changes ANYWHERE — this window, the
  // agentd window, another agent. Both facts are machine-wide, so a page holding its own stale
  // copy would keep offering to sign out of an account that is already gone.
  useDaemonEvent(client, 'auth.changed', () => void reload())

  useEffect(() => {
    void reload()
  }, [reload])

  /** The SDK's gate: the daemon performs the exchange and keeps the token, so nothing here ever
   *  holds a credential. Resolves once somebody is signed in, or at once if they already are. */
  const signIn = useCallback(async () => {
    await mountSignInGate({ client })
    await reload()
  }, [client, reload])

  /** The DAEMON's sign-out, not a local flag. It drops the identity token AND re-applies the run
   *  mode, so platform billing stops in the same step — "signed out but still metering your
   *  account" is not reachable from here. */
  const signOut = useCallback(async () => {
    await authLogout({ client })
    await reload()
  }, [client, reload])

  /** Machine-wide, and the UI says so. The model proxy is one piece of daemon state shared by
   *  every agent, so this flips the others too. No token is passed: the daemon signed the user in
   *  and kept the session, and a page that never receives a credential cannot leak one. */
  const switchMode = useCallback(
    async (next: RunMode) => {
      await setRunMode(next, { client })
      await reload()
    },
    [client, reload],
  )

  return { ...state, reload, signIn, signOut, switchMode }
}
