/* Opening the window of the agent you are building.
 *
 * WHY THIS BUTTON IS HERE AND NOT ONLY IN AGENTD. Building a window and looking at it are one
 * loop, and until now the second half lived in another application: you built here, switched to
 * the agentd window, found the agent, opened its app, and came back. The button belongs beside the
 * thing that changes it.
 *
 * THE URL IS THE SAME ONE AGENTD BUILDS (clients/ui/src/lib/artifacts.ts). An agent app is served
 * by the daemon at `/apps/<id>/` and reads three things off its own launch url:
 *
 *   scope    which agent it is, so it keys its stored session per agent rather than per origin
 *   token    the daemon's bearer token, which this window itself was launched with
 *   session  a CURRENT access token, so the new window opens signed in as the same person
 *
 * `session` is awaited rather than read, because `accessToken()` renews first when the one we hold
 * has expired. Handing over a spent token would open a window that is signed in as nobody — and
 * the daemon does not refuse that, it accepts it anonymously.
 */

import { daemonToken, identity, loadMode } from '@agentd/client'
import type { AgentRow } from './roster'

/** Can this agent be opened at all? Only an agent that declares `[app]` AND whose entry file
 *  exists gets an `app` from the daemon, so this is the whole test. */
export const hasWindow = (agent: AgentRow | null): boolean => !!agent?.app?.url

export async function appLaunchUrl(agent: AgentRow): Promise<string> {
  const app = agent.app
  if (!app?.url) throw new Error(`${agent.id} has no app window`)
  const q = new URLSearchParams({ scope: `agent:${agent.id}` })
  const token = daemonToken({})
  if (token) q.set('token', token)
  const session = await identity().accessToken()
  if (session) q.set('session', session)
  const mode = loadMode()
  if (mode) q.set('mode', mode)
  return `${location.origin}${app.url}?${q.toString()}`
}

/**
 * Open it, in a window of its own.
 *
 * A plain `window.open`, deliberately: this window is an agent app itself and has no desktop
 * bridge to ask for a native window — that privilege belongs to agentd's own renderer. A blocked
 * popup is reported by the caller rather than swallowed, because a button that silently does
 * nothing is worse than one that says the browser stopped it.
 */
export async function openAgentWindow(agent: AgentRow): Promise<void> {
  const url = await appLaunchUrl(agent)
  const opened = window.open(url, `agent-app-${agent.id}`)
  if (!opened) throw new Error('the browser blocked the pop-up — allow pop-ups for this page')
}
