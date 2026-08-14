/* Account — WHO you are. Separate from Run mode, which is who PAYS.
 *
 * Both live on this page because they are the two facts a user has to be able to see and change
 * from inside the agent they are using. Before this, signing in meant leaving the agent and
 * opening agentd, and there was no way to sign out from here at all.
 *
 * SIGNING OUT IS THE DAEMON'S SIGN-OUT, not a local flag — see usePlatform.
 */

import { useState } from 'react'
import type { AuthState } from '@agentd/client'

export function AccountSection({
  auth,
  error,
  onSignIn,
  onSignOut,
}: {
  auth: AuthState | null
  error: string
  onSignIn: () => Promise<void>
  onSignOut: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)

  const run = (fn: () => Promise<void>) => async () => {
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }

  if (error) {
    return (
      <section className="set-group">
        <h2>Account</h2>
        <div className="loading">could not read the account: {error}</div>
      </section>
    )
  }

  if (!auth?.available) {
    return (
      <section className="set-group">
        <h2>Account</h2>
        <p className="ghelp">
          This build has no accounts service, so there is nobody to sign in as. It runs on the API
          keys set below.
        </p>
      </section>
    )
  }

  const signedIn = !!auth.signedIn

  return (
    <section className="set-group">
      <h2>Account</h2>
      <p className="ghelp">
        {signedIn
          ? 'Signing out also stops platform billing — Run mode falls back to your own API keys.'
          : 'Sign in to use Cloud mode. Your own API keys keep working either way.'}
      </p>
      <div className="field">
        <div>
          <label>{signedIn ? auth.email || 'Signed in' : 'Not signed in'}</label>
          <span className="fhelp">
            {signedIn ? 'This machine is signed in to the platform.' : 'No account on this machine.'}
          </span>
        </div>
        <button className="prime-btn" disabled={busy} onClick={run(signedIn ? onSignOut : onSignIn)}>
          {signedIn ? 'Sign out' : 'Sign in'}
        </button>
      </div>
    </section>
  )
}
