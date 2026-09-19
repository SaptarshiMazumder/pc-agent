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
 *
 * EXTERNAL PROVIDERS ARE DATA, NOT BUTTONS THIS FILE KNOWS ABOUT. The accounts service publishes
 * what it accepts (`.well-known/agentd-platform` -> `providers`), and this renders one button per
 * entry. Nothing here names Google: adding Microsoft is four environment variables on the server
 * and no client release at all. A deployment with none configured gets an empty list and the card
 * renders exactly what it always did — which is why this is safe to ship to every agent.
 *
 * THE CARD IS ALSO THE REDIRECT TARGET. The provider sends the browser back to the page the flow
 * started on, so a load carrying `?code=&state=` is this component finishing a sign-in rather
 * than starting one. Handling it here means no route, no second screen, and the same code path in
 * the assistant, the web client and every agent window — all of which already render this card.
 *
 * NO INVITE FIELD ON THIS CARD. Redeeming an org invite is an IN-APP action on the Organizations
 * page (its "Join with an invite" box), not a field here: a code can only be taken by an account
 * that already exists, so it belongs AFTER sign-in, not beside it — a code on the login card just
 * reads as "join with only this", which never worked.
 */

import {
  authAuthorize,
  authCallback,
  authLogin,
  authProviders,
  oauthCallbackParams,
  type AuthProvider,
} from '@agentd/client'
import { useEffect, useState, type FormEvent } from 'react'

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
  /** External providers this deployment accepts. Empty until asked, and empty forever on a build
   *  with no accounts service — so the card degrades to the password form with no conditional. */
  const [providers, setProviders] = useState<AuthProvider[]>([])
  /** Which provider button was pressed, so only that one shows progress. */
  const [going, setGoing] = useState('')
  /** THIS LOAD IS A SIGN-IN FINISHING, not one starting: the address bar carries the provider's
   *  answer and the effect below is redeeming it. While that is true the card shows one line and
   *  no form — a form under "Continue with Google" on the way BACK from Google read as being
   *  thrown out and asked again. It only drops to the form if the exchange fails. */
  const [redeeming, setRedeeming] = useState(() => !!oauthCallbackParams())

  useEffect(() => {
    let alive = true
    void authProviders()
      .then((list) => alive && setProviders(list.filter((p) => p.kind !== 'password')))
      .catch(() => {
        /* no accounts service, or it did not answer: the password form still works */
      })
    return () => {
      alive = false
    }
  }, [])

  /* COMING BACK FROM THE PROVIDER. A load with `?code=&state=` is the second half of a flow this
     tab started; redeem it and tell the caller.

     THE QUERY IS STRIPPED FIRST, BEFORE THE EXCHANGE, and the order is the whole point. Stripping
     it afterwards raced a reload and lost: `authCallback` ends by resolving the new identity, the
     SDK reloads the window the moment that identity differs (identity.ts), and that reload is
     queued from INSIDE the await — so the page could unload before the `.then` here ever ran. The
     code and state survived into the reloaded page, sat unread in the address bar while the window
     was signed in, and reappeared on the next SIGN-OUT, when this card mounted again, found a flow
     that had already been spent, and accused the user of starting a sign-in in another tab.

     Reading the params and removing them in the same breath means nothing downstream can preserve
     them — no reload, no navigation, no second mount — whoever triggers it and whenever. */
  useEffect(() => {
    const back = oauthCallbackParams()
    if (!back) return
    stripCallbackQuery()
    let alive = true
    setBusy(true)
    void authCallback(back)
      .then(() => alive && onDone?.())
      .catch((err) => {
        if (!alive) return
        console.error('[auth] external sign-in failed', err)
        setError(String((err as Error)?.message || err))
        setRedeeming(false)
      })
      .finally(() => alive && setBusy(false))
    return () => {
      alive = false
    }
    // Once per load: the query is gone after the first pass, so a re-run would find nothing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Start an external sign-in. The URL is visited by ASSIGNING location rather than opening a
   *  window: a popup is blocked by default when the click is one await deep, and the provider's
   *  page is a full sign-in screen, not a dialog. */
  async function useProvider(id: string): Promise<void> {
    setError('')
    setGoing(id)
    try {
      const url = await authAuthorize({ provider: id, redirectUri: redirectUri() })
      location.assign(url)
    } catch (err) {
      console.error('[auth] could not start external sign-in', err)
      setError(String((err as Error)?.message || err))
      setGoing('')
    }
  }

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

  if (redeeming) {
    return (
      <div className="signin-wrap">
        <div className="signin-card">
          <div className="signin-brand">{product || 'Sign in'}</div>
          <div className="signin-sub">Signing you in…</div>
        </div>
      </div>
    )
  }

  return (
    <div className="signin-wrap">
      <form className="signin-card" onSubmit={onSubmit} noValidate>
        <div className="signin-brand">{product || 'Sign in'}</div>
        <div className="signin-sub">
          {mode === 'in' ? 'Sign in to continue' : 'Create your account'}
        </div>

        {/* ABOVE THE FORM, because for a user who has one of these it is the whole interaction —
            putting it under the password field asks them to read past the thing they are not
            going to use. The divider only appears when both doors exist. */}
        {providers.length > 0 && (
          <>
            <div className="signin-providers">
              {providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className="signin-provider"
                  disabled={busy || !!going}
                  onClick={() => void useProvider(p.id)}
                >
                  {going === p.id ? 'Redirecting…' : `Continue with ${p.label}`}
                </button>
              ))}
            </div>
            <div className="signin-or">
              <span>or</span>
            </div>
          </>
        )}

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

/** Where the provider sends the browser back: THIS page, without the query.
 *
 *  The card is rendered at whatever URL the app lives at, so the redirect target is computed
 *  rather than configured — one less value to keep in step between a deployment and its provider
 *  registration. Every distinct value here has to be registered with the provider, which is why
 *  it is deliberately the bare origin + path and never carries a token or a hash. */
function redirectUri(): string {
  const u = new URL(location.href)
  u.search = ''
  u.hash = ''
  return u.toString()
}

/** Drop `?code=&state=` from the address bar without reloading. A spent authorization code in
 *  history is replayable-looking noise, and leaving it means a refresh re-runs a dead exchange. */
function stripCallbackQuery(): void {
  try {
    const u = new URL(location.href)
    for (const k of ['code', 'state', 'scope', 'authuser', 'prompt', 'session_state', 'hd']) {
      u.searchParams.delete(k)
    }
    history.replaceState(null, '', u.toString())
  } catch {
    /* nothing to clean up in a context with no history API */
  }
}
