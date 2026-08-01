import { FormEvent, useState } from 'react'

import { login, signup } from '../lib/auth'

/**
 * Sign-in gate (accounts mode only). Signing in stores a session token; the app then connects
 * the daemon AS that account (platform.ensureDaemon appends the token) and every model call is
 * metered against the account's budget. Kept deliberately small — one card, email + password.
 */
export default function SignIn(): JSX.Element {
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
      if (mode === 'up') await signup(email, password)
      else await login(email, password)
      // success: the useAuthSession subscription re-renders App, which bootstraps + connects.
    } catch (err) {
      setError(String((err as Error)?.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-wrap">
      <form className="signin-card" onSubmit={onSubmit}>
        <div className="signin-brand">agentd</div>
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
