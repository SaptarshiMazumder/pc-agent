import { ExternalLink, RefreshCw } from 'lucide-react'
import { useState } from 'react'

import { agentLabel } from '../lib/agentPresentation'
import { appLaunchUrl } from '../lib/artifacts'
import { platform } from '../lib/platform'
import { useApp } from '../state/store'
import PageShell from './PageShell'

/**
 * An agent's OWN UI, rendered inside agentd (docs/PROTOCOL.md §9, apps-plan P4).
 *
 * The daemon side of this was already finished: it serves `/apps/<id>/`, advertises
 * `{title, url, mode}` on the agent roster, and `appLaunchUrl` mints a tokenized URL scoped to
 * the one agent. The only thing missing was somewhere to put it — every opener called
 * `window.open`, so an agent's app always LEFT the app it was installed into. Installing
 * something and then being thrown into a browser tab does not read as installing a product.
 *
 * THE FRAME IS THIRD-PARTY CODE. An app agent can come from the marketplace, so it is treated
 * as hostile to the shell around it: no top-navigation, no parent access, no referrer.
 *
 * `allow-same-origin` alongside `allow-scripts` is the one combination that deserves an
 * explanation, because together they normally defeat sandboxing — a frame that is same-origin
 * with its parent can simply reach `parent.document` and unsandbox itself. That cannot happen
 * here: the app is served by the DAEMON (`:8787`, or a `file://`/`app://` shell on desktop) and
 * is never same-origin with the page hosting it. `allow-same-origin` therefore only restores the
 * frame's access to ITS OWN origin — storage, fetch, its WebSocket back to the daemon — which it
 * cannot function without. If the shell is ever served from the daemon's own origin, this
 * combination stops being safe and the frame needs a distinct origin instead.
 */
export default function AppView() {
  const agents = useApp((s) => s.agents)
  const appAgentId = useApp((s) => s.appAgentId)
  const viewAgent = useApp((s) => s.viewAgent)
  // Bumping this remounts the iframe. There is no other way to reload a cross-origin frame:
  // its contentWindow is off limits, so `location.reload()` from here is not available.
  const [nonce, setNonce] = useState(0)

  const agent = agents.find((a) => a.id === appAgentId)
  const app = agent?.app ?? null

  if (!agent || !app) {
    return (
      <PageShell title="App" sub="No app is open.">
        <div className="page-sub">
          {agent
            ? `${agentLabel(agent.name, agent.id)} does not ship a UI. An agent gets one by adding an [app] section to its agent.toml and a ui/ folder.`
            : 'That agent is no longer installed.'}
        </div>
      </PageShell>
    )
  }

  const src = appLaunchUrl(app, agent.id)
  const title = app.title || agentLabel(agent.name, agent.id)

  // `platform.openAppWindow?.(…)` short-circuits the ENTIRE chain when the bridge is absent
  // (browser), so the fallback has to live outside the optional call, not after a `.then`.
  const openInWindow = async (): Promise<void> => {
    const res = await platform.openAppWindow?.(src, title)
    if (!res?.ok) window.open(src)
  }

  const actions = (
    <>
      <button className="btn" title="Reload the app" onClick={() => setNonce((n) => n + 1)}>
        <RefreshCw size={15} />
        Reload
      </button>
      {/* Same URL, different container. Kept as an escape hatch because some apps genuinely
          want the whole screen (and because a frame that misbehaves should stay reachable). */}
      <button className="btn" title="Open this app in its own window" onClick={() => void openInWindow()}>
        <ExternalLink size={15} />
        Open in a window
      </button>
      <button className="btn" title="Back to the agent" onClick={() => viewAgent(agent.id)}>
        Agent
      </button>
    </>
  )

  return (
    <PageShell title={title} sub={`Running as ${agentLabel(agent.name, agent.id)}`} actions={actions} className="app-embed">
      <iframe
        key={`${agent.id}:${nonce}`}
        className="app-frame"
        src={src}
        title={title}
        // No allow-top-navigation and no allow-popups-to-escape-sandbox: an embedded app must
        // never be able to replace the shell it is running inside.
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
        // The URL carries a live gateway token. Without this, following any outbound link would
        // hand that token to a third-party server in the Referer header.
        referrerPolicy="no-referrer"
        allow="clipboard-write"
      />
    </PageShell>
  )
}
