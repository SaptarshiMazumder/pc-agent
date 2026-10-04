/* The credit balance, for the composer's status strip.
 *
 * WHY IT IS ON SCREEN. Tokens tell you how much was said; credits tell you what it COST — and
 * until this existed, the only way to discover the balance was to run out and get a 402 in the
 * middle of a build. Read per agent, because an agent subscription has its own siloed balance
 * rather than drawing on the platform pool.
 *
 * NULL MEANS "WE DO NOT KNOW", not zero — not signed in, no accounts service on this build, or the
 * request failed. The strip renders nothing at all in that case, because a confident 0 shown to
 * somebody who has credits is worse than saying nothing.
 *
 * READ TWICE AFTER A RUN, deliberately. The debit happens in the model proxy's success callback,
 * which can land a moment AFTER the run ends — so a single read usually returns the pre-message
 * balance and looks like nothing was charged.
 */

import { billing, onCreditsChanged, type AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import { AGENT_ID } from './client'

/** How long after a run to re-read, covering the proxy's late debit. */
const SETTLE_MS = 1500

export function useCredits(client: AgentdClient, running: boolean): number | null {
  const [credits, setCredits] = useState<number | null>(null)

  useEffect(() => {
    let alive = true
    const read = () =>
      void billing({ client })
        .credits(AGENT_ID)
        .then((c) => {
          if (alive) setCredits(c ? c.creditsRemaining : null)
        })
        .catch(() => {
          // The strip is advisory. A failure here shows no balance, which is what null means;
          // the credits panel is where a real error about billing belongs.
          if (alive) setCredits(null)
        })

    read()
    // A run that just ENDED spent something. `running` flipping false is the trigger; the timer
    // covers a debit that settles after it.
    const timer = running ? null : setTimeout(read, SETTLE_MS)
    // A top-up happens in the credits panel, outside this component — without this the strip would
    // keep showing the pre-purchase balance until the next message.
    const off = onCreditsChanged(read)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
      off()
    }
  }, [client, running])

  return credits
}
