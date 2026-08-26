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

import { authLogin, joinOrg } from '@agentd/client'
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
  /* THE INVITE, AT THE FRONT DOOR. An org admin shares an `orginv_…` code; the person it is for
     has just installed the agent and is looking at THIS card. Redeeming used to require signing
     up, finding the Organizations page, and locating its paste box — three discoveries nobody
     tells you about. Pasting it here makes sign-up and taking the seat one motion. */
  const [invite, setInvite] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

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
    setNotice('')
    try {
      await authLogin({ email, password, signup: mode === 'up' })
      // THE SEAT, TAKEN AFTER THE CREDENTIAL AND BEFORE THE APP. Order matters twice over:
      // joining needs a signed-in account, and a join that failed must be SEEN — once the app
      // renders, "am I actually in the org?" has no obvious place to be answered.
      if (invite.trim()) {
        try {
          const org = await joinOrg({ inviteToken: invite.trim() })
          setNotice(`Joined ${org.name} — you have a seat.`)
        } catch (err) {
          // SIGNED IN, NOT SEATED — and the card says so instead of proceeding as if nothing
          // happened. "No seats left" is the admin's problem to fix, and this message is what
          // the user forwards to them. The card stays up; the person continues with Skip or a
          // corrected code, KNOWING their state either way.
          setError(
            `Signed in, but the invite failed: ${String((err as Error)?.message || err)} — ` +
              `you can continue without the seat, or fix the code and try again.`,
          )
          setInvite('')
          setBusy(false)
          return
        }
      }
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

        <label className="signin-label" htmlFor="signin-invite">
          Invite code <span className="signin-optional">optional</span>
        </label>
        <input
          id="signin-invite"
          className="signin-input"
          type="text"
          value={invite}
          onChange={(e) => setInvite(e.target.value)}
          placeholder="orginv_… — if your organization sent you one"
        />

        {error && <div className="signin-error">{error}</div>}
        {notice && <div className="signin-notice">{notice}</div>}

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

        {/* Only after a failed JOIN on a successful sign-in — the one state where the card is
            still up but the credential already works. Anywhere else a skip would be a way to
            dodge a sign-in the daemon requires. */}
        {error.startsWith('Signed in, but') && (
          <button className="signin-toggle" type="button" onClick={() => onDone?.()}>
            Continue without the seat
          </button>
        )}
      </form>
    </div>
  )
}
