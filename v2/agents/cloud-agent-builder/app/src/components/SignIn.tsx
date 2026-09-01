/* Cabbie's sign-in card.
 *
 * Same two-field card as everywhere else in the product — the DISPLAY is copied (cheap), the
 * kind of thing every surface has its own of. What differs is the one line that signs in: this
 * card calls the `login` prop, which routes to cabbie's COOKIE-mode session (see
 * agentd/cookie-session.ts), instead of the SDK's daemon path an app window uses on desktop. The
 * login LOGIC is not here and is not copied — it is `@agentd/auth`, shared with the web shell.
 *
 * INDIVIDUAL vs ENTERPRISE is a question about WHERE YOU LAND, not about what an account is —
 * there is deliberately no second account type. Enterprise is the same email+password form with
 * an optional invite-code field: the code is redeemed right after sign-in (POST /orgs/join), and
 * the session lands on the Organizations page instead of chat, where the domain-matched join
 * offers (your company's org, inferred from your email) and Create both live. This is the same
 * entry the web shell's SignIn has; it was missing here, so cabbie had no enterprise door at all.
 */

import { useState, type FormEvent } from 'react'

import { joinOrg } from '../lib/orgs'
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
  const [inviteCode, setInviteCode] = useState('')
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
      if (kind === 'enterprise') {
        // Redeem the code FIRST (if given) so the page opens on the org just joined; a failed
        // redeem still lands on the Organizations overview, whose own redeem box shows the
        // server's refusal on retry — signing in succeeded, and bouncing the user back out over a
        // mistyped code would throw that away. A blank code lands on the overview, where the
        // domain-matched "Join <company>" offer (or Create) is waiting.
        const code = inviteCode.trim()
        let orgId = ''
        if (code) {
          try {
            orgId = (await joinOrg({ inviteToken: code })).id
          } catch (joinErr) {
            console.error('[auth] invite code redeem failed', joinErr)
          }
        }
        useApp.getState().viewOrg(orgId)
      }
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
            title="Sign in to your organization — join with an invite code the first time"
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

        {kind === 'enterprise' && (
          <>
            <label className="signin-label" htmlFor="signin-invite">
              Organization invite code{' '}
              <span className="signin-optional">(first time only — members leave this blank)</span>
            </label>
            <input
              id="signin-invite"
              className="signin-input"
              type="text"
              autoComplete="off"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              placeholder="paste the code your admin sent you"
              title="A single-use code minted by your organization's admin — redeemed right after sign-in"
            />
          </>
        )}

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
