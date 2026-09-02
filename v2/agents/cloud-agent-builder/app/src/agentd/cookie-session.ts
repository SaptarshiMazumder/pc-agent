/* Cabbie's session — a BROWSER session, and the one seam that differs from Agent Builder.
 *
 * An app window on a HOSTED daemon has no runtime to ask for a token (that is desktop's
 * PlatformSession, which is None on hosted) and, opened as a plain browser tab, no launch-URL
 * token either. So cabbie holds its own session the way the agentd WEB SHELL does: a
 * `@agentd/auth` TokenManager in COOKIE MODE. The 30-day refresh token lives in an HttpOnly cookie
 * at the accounts service; this page never sees it and holds only a short access token in memory.
 *
 * THIS IS NOT A COPY OF THE WEB APP'S LOGIN. `@agentd/auth` is the shared package the web app is
 * itself built on; cabbie imports the SAME TokenManager and passes `cookies: true`. The only thing
 * written here is ~30 lines of wiring and a React hook — the login logic stays one source.
 *
 * The accounts URL is not baked: `accountsUrl()` asks the daemon (platform status), so the same
 * build signs in against whatever accounts service the daemon in front of it is configured with.
 */

import { TokenManager, memorySessionStore } from '@agentd/auth'
import { accountsUrl, forgetIdentityCache, type AuthState } from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'

/** Shaped as the SDK's `AuthState` so the sidebar, profile menu and topbar chip — all copied from
 *  Agent Builder — read it with no change. Cabbie is always hosted and always accounts-mode, so
 *  the daemon-shaped fields are constant. */
export type CabbieSession = AuthState

const listeners = new Set<() => void>()

const manager = new TokenManager({
  // Async is fine — TokenManager awaits this. The daemon in front of the page names its own
  // accounts service, so a local test and the cloud sign in against the right one with no rebuild.
  accountsUrl: () => accountsUrl({}),
  // The web holds NOTHING durable: the session is the cookie, so an in-memory store is correct.
  session: memorySessionStore(),
  cookies: true,
  clientId: 'app',
  deviceLabel: () => 'Cloud Agent Builder',
  onChange: () => {
    // Cabbie's socket runs on THIS manager's token, but orgs/credits and every other identity
    // read go through the SDK's own token cache (identity.ts), which this manager does not touch.
    // On any credential change — sign-in, sign-out, or a switch to a different account — drop that
    // SDK cache so those reads resolve as the new account instead of serving the previous user's
    // still-cached token. Without this, a switched-in user saw the old account's orgs and credits.
    forgetIdentityCache()
    listeners.forEach((l) => l())
  },
})

let started = false
let restored = false

/** Start the manager once (idempotent) and try to pick up an existing cookie session. */
export function startSession(): void {
  if (started) return
  started = true
  manager.start()
  // Cookie mode: restore() asks the accounts service to mint an access token FROM the cookie, so a
  // returning visitor is signed in without touching the card. Track when that FIRST probe finishes:
  // until it does, "not signed in" is IGNORANCE, not a fact, and the app must not send an already
  // signed-in visitor to the login card on every load. restore() resolving — signed in or not — is
  // what makes the answer real; the finally re-renders the hook. A failed probe (network) still
  // "resolves" the question to "sign in", which is the honest answer when we cannot confirm a session.
  void manager
    .restore()
    .catch(() => null)
    .finally(() => {
      restored = true
      listeners.forEach((l) => l())
    })
}

/** Has the first session probe completed? Before this, treat "signed out" as unknown, not true —
 *  the cloud shell's sign-in gate waits on it so it never flashes the login card during boot. */
export function sessionResolved(): boolean {
  return restored
}

/** The current access token, refreshed if stale — the credential the daemon socket presents. */
export function sessionToken(): Promise<string> {
  return manager.accessToken()
}

/** Subscribe to session changes (sign-in, sign-out, account switch). Returns the unsubscribe.
 *  The shim `lib/auth`'s useAuthSession subscribes here so agentd's copied OrgView refetches when
 *  the account changes — the manager's own listeners are the reactive source of truth, not the
 *  one-shot status read the shim used to do (which never updated on a switch). */
export function subscribeSession(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

/** The signed-in account id, or '' — the identity OrgView keys its refetch on. */
export function sessionAccountId(): string {
  return manager.current()?.accountId || ''
}

function snapshot(): CabbieSession {
  const p = manager.current()
  return {
    available: true, // cabbie only exists on a hosted, accounts-mode daemon
    signedIn: manager.signedIn(),
    email: p?.email || '',
    accountId: p?.accountId || '',
    mode: 'cloud', // runs on the platform's model keys
    modeLocked: true, // hosted: cloud is the only runnable option, no toggle
    canUseCloud: true,
    required: true, // the daemon requires an account to run
  }
}

/**
 * The window's auth state, shaped like Agent Builder's `usePlatform` so the Gate, the profile menu
 * and the topbar chip read it unchanged. `onSignedIn` is handed the connected client so the socket
 * re-dials with the new session the moment sign-in succeeds.
 */
export function useCabbieSession(onCredentialChange?: () => void): {
  auth: CabbieSession
  /** Has the first session probe finished? The shell waits on this before deciding signed-out. */
  resolved: boolean
  wantsSignIn: boolean
  error: string
  signIn: () => void
  signedIn: () => void
  signOut: () => Promise<void>
  doLogin: (email: string, password: string, signup: boolean) => Promise<void>
} {
  const [auth, setAuth] = useState<CabbieSession>(snapshot)
  const [resolved, setResolved] = useState(restored)
  const [wantsSignIn, setWantsSignIn] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    startSession()
    const sync = () => {
      setAuth(snapshot())
      setResolved(restored)
    }
    listeners.add(sync)
    sync()
    return () => {
      listeners.delete(sync)
    }
  }, [])

  const signIn = useCallback(() => {
    setError('')
    setWantsSignIn(true)
  }, [])

  const signedIn = useCallback(() => {
    setWantsSignIn(false)
    onCredentialChange?.()
  }, [onCredentialChange])

  const doLogin = useCallback(
    async (email: string, password: string, signup: boolean) => {
      // Throws with the accounts service's own message on refusal, so the card can show it.
      await manager.login({ email, password, signup })
      onCredentialChange?.()
    },
    [onCredentialChange],
  )

  const signOut = useCallback(async () => {
    await manager.logout()
    onCredentialChange?.()
  }, [onCredentialChange])

  return { auth, resolved, wantsSignIn, error, signIn, signedIn, signOut, doLogin }
}
