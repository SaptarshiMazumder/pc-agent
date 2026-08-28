/* Cabbie's sign-in card.
 *
 * Same two-field card as everywhere else in the product — the DISPLAY is copied (cheap), the
 * kind of thing every surface has its own of. What differs is the one line that signs in: this
 * card calls the `login` prop, which routes to cabbie's COOKIE-mode session (see
 * agentd/cookie-session.ts), instead of the SDK's daemon path an app window uses on desktop. The
 * login LOGIC is not here and is not copied — it is `@agentd/auth`, shared with the web shell.
 */

import { useState, type FormEvent } from 'react'

import './auth.css'

export default function SignIn({
  product = '',
  login,
  onDone,
}: {
  /** What the user is signing in TO. Shown as the card's title. */
  product?: string
  /** Cookie-mode sign-in. Throws with the accounts service's own message on refusal. */
  login: (email: string, password: string, signup: boolean) => Promise<void>
  /** Called once the credential is stored, so the caller can re-dial the socket. */
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
    // OUR validation, not the browser's. noValidate below because Chromium's native check blocks
    // the submit with only a transient tooltip — which reads as the button doing nothing. Every
    // failure goes through the same red line instead.
    if (!email.trim() || !email.includes('@')) {
      setError('enter a valid email address')
      return
    }
    if (mode === 'up' && password.length < 8) {
      setError('password must be at least 8 characters')
      return
    }
    setBusy(true)
    try {
      await login(email, password, mode === 'up')
      onDone?.()
    } catch (err) {
      console.error('[auth] sign-in failed', err)
      setError(String((err as Error)?.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-wrap">
      <form className="signin-card" onSubmit={onSubmit} noValidate>
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
