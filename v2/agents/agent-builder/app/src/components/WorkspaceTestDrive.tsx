/* The Test-drive pane — the frame for a screen whose mechanisms do not exist yet, saying so.
 *
 * WHAT IT WILL BE (the design's 3f): talk to the finished agent the way a stranger would, in a
 * throwaway session that never touches its memory; watch a trace of every turn — which tools
 * ran, how long, what failed; standing checks asserting the agent stays in its own directory,
 * uses only granted tools, confirms destructive acts; and a "send to builder" that hands a bad
 * turn straight back into the build conversation.
 *
 * WHY NONE OF THAT IS LIVE: every piece needs a mechanism this window does not have. A
 * throwaway session needs the agent's window to accept a session override it does not read;
 * the trace needs another agent's run events, which this window's scoped connection is
 * deliberately not sent; the checks are assertions over that stream. Rendering sample turns or
 * green checks here would be the UI lying about an agent nobody has tested.
 *
 * WHAT IS REAL TODAY: opening the agent's own window and talking to it yourself — the same
 * mechanism as every Open button, offered here because it IS the manual version of this
 * screen. The chrome around it is the frame the real mechanisms land in.
 */

import { ChartNoAxesColumn, ExternalLink, Play, ShieldCheck } from 'lucide-react'

import { hasWindow } from '../agentd/app-window'
import type { AgentRow } from '../agentd/roster'
import { useOpenAgent } from './MyAgentsView'

export function WorkspaceTestDrive({ agent }: { agent: AgentRow }) {
  const { opening, error, open } = useOpenAgent()

  return (
    <div className="wst">
      <div className="wst-stage">
        <div className="wst-card">
          <Play size={22} />
          <h3 className="wst-title">Test drive is not wired yet</h3>
          <p className="wst-copy">
            This screen will run {agent.name || agent.id} in a throwaway session — talked to as a
            stranger, never written to its memory — with a trace of what every turn actually did,
            and a way to hand a bad one straight back to this conversation.
          </p>
          {hasWindow(agent) ? (
            <>
              <p className="wst-copy">Until then, the manual version works today:</p>
              <button
                className="prime-btn"
                disabled={!!opening}
                onClick={() => void open(agent)}
                title={opening ? 'Opening…' : `Open ${agent.name || agent.id} and talk to it yourself`}
              >
                <ExternalLink size={15} />
                {opening ? 'Opening…' : 'Open the window and try it'}
              </button>
            </>
          ) : (
            <p className="wst-copy">
              This agent has no window — try it by talking to it from the main agentd chat.
            </p>
          )}
          {error && <div className="page-error">{error}</div>}
        </div>
      </div>

      <aside className="wst-trace">
        <h3 className="lp-side-label">
          <ChartNoAxesColumn size={13} />
          Trace
        </h3>
        <p className="lp-side-empty">No test runs yet — each turn's tool calls and timings land here.</p>

        <h3 className="lp-side-label">
          <ShieldCheck size={13} />
          Standing checks
        </h3>
        <p className="lp-side-empty">
          Stays in its own directory · only granted tools · confirms destructive acts — asserted
          over real runs once the trace exists, not before.
        </p>
      </aside>
    </div>
  )
}
