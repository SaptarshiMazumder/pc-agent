/* The connection, and the one place that answers "which agent is this window?".
 *
 * Agent Builder is a pure CLIENT of the daemon: it connects over the same WebSocket any agent app
 * uses, and every capability it has is one the daemon granted. Two of those are unusual and the
 * rest of this app assumes them:
 *
 *   * it may READ other agents (roster, detail, files) — the daemon lists agent-builder in
 *     CROSS_AGENT_READS. Reads only: it can never chat or write AS another agent.
 *   * it may read and write CONFIG — so the Settings view is the real agentd settings, and BYOK
 *     works from inside a shipped agent.
 *
 * Nothing here has a backend of its own, and never could. That is the platform invariant.
 */

import { fromPage, type AgentdClient } from '@agentd/client'
import { useEffect, useMemo, useRef, useState } from 'react'

export type Status = 'connecting' | 'open' | 'closed'

/** Whose window this is.
 *
 *  The connect URL carries `scope=agent:<id>`, and the daemon strips that prefix before it forces
 *  the agent onto our requests — so anything we key BY agent id has to strip it too, or it writes
 *  a block under "agent:<id>" that the resolver never looks for. */
export const AGENT_ID =
  (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '') || 'agent-builder'

/** One client for the life of the page, plus its connection state.
 *
 *  `fromPage()` reads the token and scope the opener put in the URL. The status is not decoration:
 *  the boot sequence hangs off the first `open`, and re-runs the identity probe on every later one
 *  because signing in re-dials the socket with a new session. */
export function useClient(): { client: AgentdClient; status: Status } {
  const client = useMemo(() => fromPage(), [])
  const [status, setStatus] = useState<Status>('connecting')
  useEffect(() => client.onStatus((s) => setStatus(s as Status)), [client])
  return { client, status }
}

/** Subscribe to a daemon broadcast for as long as the component is mounted.
 *
 *  The unsubscribe is the whole reason this exists. `client.on` returns one, and without calling
 *  it every re-render stacks another handler: the roster reloads twice, then four times, then
 *  eight, and a streamed delta is appended once per copy. */
export function useDaemonEvent(
  client: AgentdClient,
  event: string,
  handler: (payload: any) => void,
): void {
  // The handler is read through a ref so the subscription does not re-open every time the caller
  // passes a fresh closure — which is every render, for an inline arrow function.
  const held = useRef(handler)
  held.current = handler
  useEffect(() => client.on(event, (p: any) => held.current(p)), [client, event])
}
