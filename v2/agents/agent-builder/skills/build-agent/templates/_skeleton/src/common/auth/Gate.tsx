/* Sign in first, when this daemon demands it — or when this agent does, whatever the daemon says.
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
 * IT RENDERS THE APP WHILE IT IS ASKING. A blank screen during a status probe is indistinguishable
 * from a broken window, and the probe is a round trip to a daemon that may not be there.
 */

import { authStatus, type AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import SignIn from './SignIn'

/** What the probe concluded. `blocked` is the dead end below. */
type Verdict = 'pending' | 'through' | 'sign-in' | 'blocked'

export default function Gate({
  client,
  product = '',
  require: demand = false,
  children,
}: {
  client?: AgentdClient
  /** What the user is signing in to. Shown on the card. */
  product?: string
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
  const [verdict, setVerdict] = useState<Verdict>('pending')

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

  useEffect(check, [check])

  if (verdict === 'sign-in') return <SignIn product={product} onDone={check} />
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
