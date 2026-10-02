/* Sign in first, when this daemon demands it — or when this agent does, whatever the daemon says.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. If you need something it does not expose, add it there so every agent gets it.
 *
 * WHAT THIS REPLACED. `signInFirst()` awaited a vanilla-DOM gate that painted itself over the page
 * BEFORE the app rendered, which is why every scaffolded agent's entry point had to be an async
 * IIFE that rendered nothing until it resolved. The gate is agentd's React card now, so it renders
 * inside the app like anything else and the entry point goes back to being one `root.render`.
 *
 * `required`, NOT `available`. An accounts service EXISTING is not the same question as this
 * daemon demanding an account — a desktop daemon accepts the machine token and needs none, and
 * conflating the two is what once put a login form in front of every window on a local install.
 * Only the daemon knows, so only the daemon is asked.
 *
 * ...UNLESS THE AGENT ITSELF DEMANDS ONE — see `require`.
 *
 * IT NEVER SHOWS THE APP AS A GUESS. It used to render the app while the probe was out, on the
 * theory that a blank screen reads as a broken window. What that produced, on every signed-out
 * visit and on every return from Google, was the app for a beat, then the sign-in card, then the
 * app again once the page started over — three screens for one sign-in, and the middle one made
 * people think they had been thrown out. The probe's frames now show a card with the product's
 * name on it and nothing else: the honest state while the answer is on its way, and the same card
 * the sign-in form appears on, so a signed-out visit is one screen that fills in.
 *
 * A RETURN FROM AN EXTERNAL PROVIDER IS KNOWN BEFORE ANY PROBE. The address bar says so
 * (`?code=&state=`), so the gate goes straight to the card — which redeems the code and, on
 * success, is the last thing drawn before the app.
 *
 * A SIGN-OUT IS COVERED, TOO. The button lives deep inside the app, and between its click and
 * the page starting over the app kept drawing itself signed out. The account menu raises a flag
 * (sign-out-transition.ts); the gate draws the card over those frames.
 */

import { authStatus, oauthCallbackParams, onIdentityChanged, type AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import SignIn from './SignIn'
import { SignInWaiting } from './SignInWaiting'
import { signOutTransition } from './sign-out-transition'

/** What the probe concluded. `blocked` is the dead end below. */
type Verdict = 'pending' | 'through' | 'sign-in' | 'blocked'

export default function Gate({
  client,
  product = '',
  mark,
  require: demand = false,
  children,
}: {
  client?: AgentdClient
  /** What the user is signing in to. Shown on the card. */
  product?: string
  /** The product's mark, drawn on the card and the waiting screen. Optional. */
  mark?: ReactNode
  /**
   * THIS AGENT demands an identity, whatever the deployment would settle for.
   *
   * `AuthState.required` is the DAEMON's answer to "must anyone sign in here", and it is false on
   * every desktop install — the machine token already authorises that window, so the gate steps
   * aside. An agent whose every run costs somebody money, or writes into somebody's workspace,
   * cannot let the deployment decide that: the same package has to behave identically on a laptop
   * and on the hosted daemon, and "who is this?" is the agent's question, not the host's.
   *
   * Set it here rather than inferring it from `[app] mode` or from being hosted, because an agent
   * that needs an account needs it for reasons only the agent knows.
   */
  require?: boolean
  children: ReactNode
}) {
  // A load carrying the provider's answer is a sign-in finishing, not a page to probe: the card
  // is the right first frame, and it knows how to finish the job.
  const [verdict, setVerdict] = useState<Verdict>(() => (oauthCallbackParams() ? 'sign-in' : 'pending'))
  const [signingOut, setSigningOut] = useState(signOutTransition.current)

  const check = useCallback(() => {
    void authStatus({ client })
      .then((s) => {
        // WHO DECIDES: `s.required` is the deployment's answer, `require` is the product's, and
        // either one is enough. A hosted daemon must not be able to waive an agent's own rule,
        // and an agent must not have to detect that it is hosted in order to keep it.
        const demanded = demand || !!s.required
        if (!demanded || s.signedIn) return setVerdict('through')
        setVerdict(s.available ? 'sign-in' : 'blocked')
      })
      .catch(() => {
        // The daemon is unreachable. NOT a reason to demand a login: the app reports its own
        // connection trouble, and a sign-in form is the wrong explanation for it.
        setVerdict('through')
      })
  }, [client, demand])

  useEffect(() => {
    // Not on a return from the provider: the card redeems the code first and calls `check`
    // itself when that is done. Probing now would only draw the form over the finishing flow.
    if (!oauthCallbackParams()) check()
    // RE-PROBE ON EVERY AUTH CHANGE, not only at mount. A window that signed out used to keep
    // rendering the app — signed out, with a "○" avatar and a "Local" persona — because this
    // gate had already answered "through" once and was never asked again. The SDK reloads the
    // window on a change after boot; this covers the ones at boot, and desktop's socket
    // broadcast.
    const offSocket = client?.on('auth.changed', check)
    const offIdentity = onIdentityChanged(check)
    const offLeaving = signOutTransition.subscribe(setSigningOut)
    return () => {
      offSocket?.()
      offIdentity()
      offLeaving()
    }
  }, [check, client])

  if (signingOut) return <SignInWaiting product={product} note="Signing out…" mark={mark} />
  if (verdict === 'pending') return <SignInWaiting product={product} note="" mark={mark} />
  if (verdict === 'sign-in') return <SignIn product={product} mark={mark} onDone={check} />
  if (verdict === 'blocked') return <Blocked product={product} />
  return <>{children}</>
}

/* THE DEAD END: sign-in is demanded and this daemon has no accounts service to demand it from.
 *
 * The same card, with no form — because the honest thing to render is neither a login that can
 * post nowhere nor an app that quietly runs as nobody. Reached only via `require`, so an ordinary
 * BYOK app never sees it: without that flag a daemon with no accounts service reports
 * `required: false` and the gate steps aside. */
function Blocked({ product }: { product: string }) {
  const name = product || 'This app'
  return (
    <div className="signin-wrap">
      <div className="signin-card">
        <div className="signin-brand">{name}</div>
        <div className="signin-sub">{name} runs on your account, so it cannot be used signed out.</div>
        <div className="signin-error">
          This daemon has no accounts service configured, so there is nowhere to sign in. Point it
          at one (AGENTD_ACCOUNTS_URL, or accounts.api_base in its config) and reload.
        </div>
      </div>
    </div>
  )
}
