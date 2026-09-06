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
 * Build the agent's window, then open it — so the button means "show me my current source"
 * rather than "show me whatever was last compiled".
 *
 * WHY THE BUILD IS PART OF THE BUTTON. `app/` is source and `ui/` is what the daemon serves, so
 * opening without building shows the last build however new the source is. The reload itself is
 * honest — the daemon serves `ui/` with `Cache-Control: no-store`, so you always get what is on
 * disk — but "what is on disk" is only current if something ran vite. Leaving that to whoever
 * remembers is how you press a button, see the old screen, and have nothing to blame.
 *
 * A FAILED BUILD DOES NOT OPEN A WINDOW. Showing the previous build after an error would be the
 * worst of both: it looks like the change did nothing, when in fact it did not compile.
 *
 * An agent with a hand-written `ui/` has nothing to build; the caller decides that (it can see the
 * file tree) and skips straight to opening.
 */
export async function buildAndOpen(
  client: { invokeTool(name: string, params: Record<string, unknown>): Promise<unknown> },
  agent: AgentRow,
  build: boolean,
): Promise<void> {
  // GRAB THE TAB NOW, inside the click — before the build's `await` spends the user gesture and
  // the browser blocks the pop-up (ported from cabbie, where this shipped first): opening after
  // an await reads to the browser as a script pop-up (blocked), opening synchronously reads as
  // a click (allowed). Desktop goes through the bridge and needs no pre-open; a null (pop-ups
  // hard-blocked) falls through to the helpful error in openAgentWindow.
  const pre = host()?.openAppWindow ? null : window.open('', `agent-app-${agent.id}`)
  if (pre) {
    // Something to look at while vite runs — a blank tab reads as a hang.
    pre.document.write(
      '<!doctype html><title>Opening…</title>' +
        '<body style="font:14px system-ui;margin:0;display:grid;place-items:center;height:100vh;' +
        'color:#8a94a3;background:#0b0e14">Building and opening the agent…</body>',
    )
  }
  try {
    if (build) await buildApp(client, agent)
    await openAgentWindow(agent, pre)
  } catch (e) {
    pre?.close() // a tab we opened but never navigated is worse than none
    throw e
  }
}

/** Just the build — the first half of buildAndOpen, split out so the in-app preview pane can
 *  rebuild without popping a window. Throws with the tool's own report — vite's error, naming
 *  the file and line — which the caller shows verbatim. There is nothing useful this layer
 *  could add to it. */
export async function buildApp(
  client: { invokeTool(name: string, params: Record<string, unknown>): Promise<unknown> },
  agent: AgentRow,
): Promise<void> {
  await client.invokeTool('build_app', { agent_id: agent.id })
}

/** The desktop bridge, when this window is running inside the desktop app. */
type Host = {
  openAppWindow?: (url: string, title?: string) => Promise<{ ok: boolean; error?: string }>
}
const host = (): Host | undefined => (globalThis as { agentdHost?: Host }).agentdHost

/**
 * Open it, in a window of its own.
 *
 * ASK THE DESKTOP APP, DO NOT USE `window.open`. The two are not variations on one idea: the
 * desktop app builds a real window with the agent-app preload — the same window its own "Open app"
 * button produces, which keeps receiving fresh access tokens for as long as it is open — whereas
 * `window.open` is deliberately routed to the SYSTEM BROWSER by the shell's window-open handler.
 * That route opens the app outside the desktop app entirely, with no token pushing, which is what
 * the first version of this did and why it "opened in Chrome and broke".
 *
 * The fallback is for a real browser tab, where there is no desktop app to ask and external is not
 * a downgrade but the only meaning "open" can have.
 */
export async function openAgentWindow(agent: AgentRow, pre?: Window | null): Promise<void> {
  const url = await appLaunchUrl(agent)
  const bridge = host()?.openAppWindow
  if (bridge) {
    const res = await bridge(url, agent.app?.title || agent.name || agent.id)
    // Reported, not swallowed. The desktop app refuses this call from any window that is not this
    // one, and a button that silently does nothing is worse than one that says why.
    if (!res?.ok) throw new Error(res?.error || 'the desktop app would not open that window')
    return
  }
  // The tab grabbed on click (buildAndOpen) — navigate it now that the url is ready.
  if (pre && !pre.closed) {
    pre.location.href = url
    return
  }
  const opened = window.open(url, `agent-app-${agent.id}`)
  if (!opened) throw new Error('the browser blocked the pop-up — allow pop-ups for this page')
}
