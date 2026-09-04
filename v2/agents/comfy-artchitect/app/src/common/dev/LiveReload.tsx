/* Reload this window when its agent is rebuilt.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source.
 *
 *     <LiveReload client={client} />                     // an agent's own window
 *     <LiveReload client={client} agentId="my-agent" />  // a HOST window — see below
 *
 * WHY IT EXISTS. `app/` is source and `ui/` is what the daemon serves, so a window shows whatever
 * was last compiled — and nothing about that changes underneath it. While an agent is being built,
 * that meant reopening its window by hand after every single change to see whether the change was
 * any good. The daemon now says when it rebuilt an agent, and this listens.
 *
 * AN AGENT'S OWN WINDOW PASSES NO `agentId`, and that is deliberate rather than an omission. The
 * daemon decides who hears this: `_scoped_event_allowed` compares the event's agentId to the one
 * the connection is scoped to, so an arriving `app.rebuilt` is already known to be about THIS
 * agent. Re-checking would put the rule in two places, and two copies of one rule are two things
 * to keep in step.
 *
 * A HOST WINDOW MUST PASS ONE, and this is the exception that proves the rule above. That policy
 * governs agent-SCOPED connections only; a host connection — Agent Builder's own window, which
 * needs to see every agent in order to build them — bypasses it and receives everything. Without
 * an id there, this would reload Agent Builder whenever ANY agent was rebuilt, including the one
 * you are building, in the middle of the conversation you are building it in. So the filter is
 * not a second copy of the daemon's rule: it is the rule for the connections the daemon does not
 * apply it to.
 *
 * IT IS INERT IN A PUBLISHED AGENT. The event comes from `build_app`, a tool that exists only
 * where the agent-authoring plugin is installed. On the machine of somebody who merely downloaded
 * this agent, nothing can emit it — so there is no mode to switch off and no flag to forget.
 *
 * A FULL RELOAD, not a hot swap. The window is showing a bundle that has been replaced on disk, so
 * there is nothing to patch in place — and a page that reloaded only some of itself would be a
 * window whose parts disagree about which build they came from.
 */

import { useEffect } from 'react'
import type { AgentdClient } from '@agentd/client'

export default function LiveReload({
  client,
  agentId,
}: {
  client?: AgentdClient
  /** Only for a HOST window, which the daemon does not scope. Omit it in an agent's own window —
   *  see the note above about where this rule belongs. */
  agentId?: string
}) {
  useEffect(() => {
    if (!client) return
    // `client.on` returns its own unsubscribe, so React tears this down with the component. A
    // missing cleanup here would stack another listener on every reconnect, and the reload would
    // fire once per listener.
    return client.on('app.rebuilt', (payload: any) => {
      // No id given => this connection is agent-scoped and the daemon already decided.
      if (agentId && payload?.agentId !== agentId) return
      location.reload()
    })
  }, [client, agentId])

  return null
}
