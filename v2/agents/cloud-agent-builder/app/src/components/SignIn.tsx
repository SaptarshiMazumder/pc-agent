/* Cabbie's sign-in card.
 *
 * Same two-field card as everywhere else in the product — the DISPLAY is copied (cheap), the
 * kind of thing every surface has its own of. What differs is the one line that signs in: this
 * card calls the `login` prop, which routes to cabbie's COOKIE-mode session (see
 * agentd/cookie-session.ts), instead of the SDK's daemon path an app window uses on desktop. The
 * login LOGIC is not here and is not copied — it is `@agentd/auth`, shared with the web shell.
 *
 * INDIVIDUAL vs ENTERPRISE is a question about WHERE YOU LAND, not about what an account is —
 * there is deliberately no second account type. Both are the same email+password form; Enterprise
 * simply lands the session on the Organizations page instead of chat, where the domain-matched
 * join offers (your company's org, inferred from your email), the invite-code redeem box, and
 * Create all live. Redeeming an invite is an IN-APP action on that page, NOT a field on this card:
 * a code can only be taken by an account that already exists, so it belongs after sign-in, not
 * beside it — a code on the login card just reads as "join with only this", which never worked.
 */

import { useState, type FormEvent } from 'react'

import { useApp } from '../state/store'
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
  const [kind, setKind] = useState<'individual' | 'enterprise'>('individual')
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
      // Enterprise lands on the Organizations page (overview), where the domain-matched
      // "Join <company>" offer, the invite-code redeem box, and Create all live. Redemption is an
      // in-app action there — a code can only be taken by the account that now exists.
      if (kind === 'enterprise') useApp.getState().viewOrg('')
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

        <div className="signin-kind" role="tablist" aria-label="Account kind">
          <button
            type="button"
            role="tab"
            aria-selected={kind === 'individual'}
            className={`signin-kind-btn ${kind === 'individual' ? 'active' : ''}`}
            onClick={() => setKind('individual')}
            title="Sign in to your own workspace"
          >
            Individual
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={kind === 'enterprise'}
            className={`signin-kind-btn ${kind === 'enterprise' ? 'active' : ''}`}
            onClick={() => setKind('enterprise')}
            title="Sign in to your organization"
          >
            Enterprise
          </button>
        </div>

        <div className="signin-sub">
          {kind === 'enterprise'
            ? mode === 'in'
              ? 'Sign in to your organization'
              : 'Create your account and join your organization'
            : mode === 'in'
              ? 'Sign in to continue'
              : 'Create your account'}
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
          placeholder={kind === 'enterprise' ? 'you@yourcompany.com' : 'you@example.com'}
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
