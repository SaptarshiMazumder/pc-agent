import { FormEvent, useState } from 'react'

import { login, signup } from '../lib/auth'
import { joinOrg } from '../lib/orgs'
import { useApp } from '../state/store'

/**
 * Sign-in gate (accounts mode only). Signing in stores a session token; the app then connects
 * the daemon AS that account (platform.ensureDaemon appends the token) and every model call is
 * metered against the account's budget.
 *
 * INDIVIDUAL vs ENTERPRISE is a question about WHERE YOU LAND, not about what an account is —
 * there is deliberately no second account type. An enterprise user is an ordinary account plus
 * an org membership (tenancy E5), so the Enterprise tab is the same email+password form with an
 * optional invite-code field: the code is redeemed right after sign-in (POST /orgs/join, the
 * same call the org overview's redeem box makes), and the session lands on the organization
 * page instead of the chat. Members who already joined leave the code blank; the org page also
 * surfaces the domain-matched join offers for accounts whose email domain an org claimed.
 */
export default function SignIn(): JSX.Element {
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
    // OUR validation, not the browser's. The form says noValidate because Chromium's native
    // check BLOCKS the submit with only a transient tooltip -- to a user that reads as the
    // button doing nothing. Every failure goes through the same red line instead.
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
      if (mode === 'up') await signup(email, password)
      else await login(email, password)
      // success: the useAuthSession subscription re-renders App, which bootstraps + connects.
      if (kind === 'enterprise') {
        // Land on the organization page. The code (if given) is redeemed FIRST so the page
        // opens on the org just joined; a failed redeem still lands on the org overview, whose
        // own redeem box shows the server's refusal on retry — signing in succeeded, and
        // bouncing the user back out over a mistyped code would throw that away.
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
    } catch (err) {
      // The full object, not just .message: a rejected IPC call and a CORS failure both
      // stringify to something useless, and this is the one place the cause is still in hand.
      console.error('[auth] sign-in failed', err)
      setError(String((err as Error)?.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-wrap">
      <form className="signin-card" onSubmit={onSubmit} noValidate>
        <div className="signin-brand">agentd</div>

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
              <span className="signin-label-hint">(first time only — members leave this blank)</span>
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
