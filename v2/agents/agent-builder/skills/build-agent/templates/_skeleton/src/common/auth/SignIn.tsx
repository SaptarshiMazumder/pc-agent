/* The sign-in card — agentd's, copied.
 *
 * COPIED FROM clients/ui/src/components/SignIn.tsx: same two-field card, same sign-in/create
 * toggle, same error handling. An agent's login screen and the assistant's are one screen.
 *
 * WHAT THIS REPLACED. Agents used to call `mountSignInGate` — a vanilla-DOM gate in the SDK,
 * written for the vanilla templates that no longer exist. agentd never used it; it has always had
 * this React card. So there were two login screens, and only one of them was the product's.
 *
 * IT IS A COMPONENT, NOT A MOUNT. The old gate built its own DOM and had to run BEFORE the app
 * rendered, which is why the scaffold's entry point awaited it. A component renders inside the
 * app like everything else — see `signedOut` in useAuth and the `<Gate>` below.
 */

import { authLogin } from '@agentd/client'
import { useState, type FormEvent } from 'react'

import './auth.css'

export default function SignIn({
  product = '',
  onDone,
}: {
  /** What the user is signing in TO. Shown as the card's title. */
  product?: string
  /** Called once the credential is stored, so the caller can re-read its auth state. */
  onDone?: () => void
}) {
  const [mode, setMode] = useState<'in' | 'up'>('in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function onSubmit(e: FormEvent): Promise<void> {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await authLogin({ email, password, signup: mode === 'up' })
      onDone?.()
    } catch (err) {
      // The full object, not just .message: a rejected call and a CORS failure both stringify to
      // something useless, and this is the one place the cause is still in hand.
      console.error('[auth] sign-in failed', err)
      setError(String((err as Error)?.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-wrap">
      <form className="signin-card" onSubmit={onSubmit}>
        <div className="signin-brand">{product || 'Sign in'}</div>
        <div className="signin-sub">
          {mode === 'in' ? 'Sign in to continue' : 'Create your account'}
        </div>

        <label className="signin-label" htmlFor="signin-email">
          Email
        </label>
        <input
          id="signin-email"
          className="signin-input"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />

        <label className="signin-label" htmlFor="signin-password">
          Password
        </label>
        <input
          id="signin-password"
          className="signin-input"
          type="password"
          autoComplete={mode === 'up' ? 'new-password' : 'current-password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={mode === 'up' ? 'at least 8 characters' : '••••••••'}
          required
        />

        {error && <div className="signin-error">{error}</div>}

        <button className="signin-btn" type="submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'in' ? 'Sign in' : 'Create account'}
        </button>

        <button
          className="signin-toggle"
          type="button"
          onClick={() => {
            setError('')
            setMode((m) => (m === 'in' ? 'up' : 'in'))
          }}
        >
          {mode === 'in' ? 'Create an account' : 'Have an account? Sign in'}
        </button>
      </form>
    </div>
  )
}
