/**
 * STANDALONE APPS — the agents that are surfaces of the product rather than agents you picked.
 *
 * Agent Builder is the first of them: building agents is a FEATURE of agentd, so it belongs in
 * the navigation next to Projects, not on a shelf between the weather agent and one the user
 * wrote. Cloud Agent Builder is the same thing on the web.
 *
 * NOTHING HERE NAMES THEM. The agent declares it in its own agent.toml —
 *
 *     [app]
 *     standalone = true
 *
 * — the registry parses it, the gateway ships it on `app.standalone`, and this module is the one
 * place that turns that fact into "where does it go". The alternative was every client testing
 * `id === 'agent-builder'`: the same guess copied into the shell, both builder windows and the
 * shelf, each free to disagree, and all four wrong the day somebody forks or renames it.
 *
 * TWO CONSEQUENCES, ONE FACT. A standalone app is (a) offered as its own destination and (b)
 * absent from agent lists. Both read `standalone`, so an agent that stops declaring it goes back
 * to being an ordinary listed agent with no code change anywhere.
 */

import type { AgentInfo } from '../gateway/protocol'
import { appLaunchUrl } from './artifacts'
import { isDesktop } from './host'
import { platform } from './platform'

/** Is this agent a surface of the product rather than one of the user's agents?
 *
 *  Requires a REAL app: `_agent_app` returns null when the declared entry file is missing, so an
 *  agent whose UI failed to build has no `app` at all. Without this check it would vanish from
 *  the lists (standalone) while offering no button to reach it (no app) — invisible in both
 *  places, which is the one outcome worse than either. */
export function isStandaloneApp(agent: AgentInfo): boolean {
  return !!agent.app?.standalone
}

/** The agents that belong in lists, shelves and pickers — everything the user actually chose. */
export function listableAgents(agents: readonly AgentInfo[]): AgentInfo[] {
  return agents.filter((a) => !isStandaloneApp(a))
}

/**
 * The product surfaces to render as destinations — ONE per surface, not one per agent.
 *
 * Agent Builder and Cloud Agent Builder both declare `surface = "agent-builder"`: they are two
 * implementations of a single place — the desktop one where you own the machine, the fenced one
 * where you own an account on somebody else's. A desktop daemon offers BOTH (the hosted one is
 * harmless there and useful for testing the web surface), which would otherwise put two buttons
 * called "Agent Builder" side by side and make the user guess.
 *
 * WHICH ONE WINS is decided by the author's own `requires_local`, not by a list here: on a
 * desktop, prefer the implementation that asked for a machine of its own, because this IS that
 * machine; anywhere else prefer the one that never asked, because the other is not offered there
 * at all. Falling back to the first candidate means a surface always renders, even if the only
 * implementation present is the "wrong" kind.
 */
export function standaloneApps(agents: readonly AgentInfo[]): AgentInfo[] {
  const bySurface = new Map<string, AgentInfo[]>()
  for (const a of agents.filter(isStandaloneApp)) {
    const key = a.app?.surface || a.id
    const group = bySurface.get(key)
    if (group) group.push(a)
    else bySurface.set(key, [a])
  }
  return [...bySurface.values()].map(
    (group) =>
      group.find((a) => !!a.app?.requiresLocal === isDesktop) ?? group[0]
  )
}

/**
 * Open one, honoring the author's declared `mode` — the same three-way the agent page uses:
 *
 *   window  -> its own desktop window, when a bridge is there to open one
 *   (no bridge) -> a browser tab, which is what "the web url" means on the web
 *   browser -> embedded as a page inside agentd, via the caller's `openEmbedded`
 *
 * `openEmbedded` is passed in rather than imported so this module stays free of the store — the
 * builder windows have their own and do not share ours.
 */
export async function launchStandaloneApp(
  agent: AgentInfo,
  openEmbedded?: (agentId: string) => void
): Promise<void> {
  const app = agent.app
  if (!app) return
  if (app.mode === 'window') {
    const res = await platform.openAppWindow?.(appLaunchUrl(app, agent.id), app.title)
    if (res?.ok) return
    window.open(appLaunchUrl(app, agent.id)) // no bridge (browser) — fall back to a tab
    return
  }
  if (openEmbedded) openEmbedded(agent.id)
  else window.open(appLaunchUrl(app, agent.id))
}
