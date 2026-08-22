/* The header says WHAT THIS SCREEN IS ABOUT, in two lines: a small label over the subject.
 *
 * It holds only what is about this view. Actions live where their subject is — the ones that act
 * on an agent are in the inspector, next to that agent's files.
 */

import type { WhoAmI } from '../agentd/platform'
import type { AgentRow } from '../agentd/roster'

export function Topbar({
  agent,
  who,
  canTogglePanel,
  panelOpen,
  onTogglePanel,
  onRestart,
  restarting,
  restartNote,
}: {
  agent: AgentRow | null
  who: WhoAmI
  canTogglePanel: boolean
  panelOpen: boolean
  onTogglePanel: () => void
  /** Restart the daemon. HERE as well as in Settings because this is where the reason to
   *  restart occurs: you have just watched the agent write a plugin, and its Python is already
   *  imported. Making that a trip through a settings page is three clicks away from the moment
   *  you need it, every time. */
  onRestart: () => void
  restarting: boolean
  restartNote: string
}) {
  return (
    <header className="topbar">
      {/* THE WAY BACK USED TO LIVE HERE. Collapsing the sidebar removed it entirely, taking its
          own expand control with it, so this button existed to undo that. The sidebar now
          collapses to agentd's 64px icon rail instead of disappearing — the expand control is
          always on screen — and a second door to it would be a button that is never needed. */}
      <div className="head-text">
        <span className="eyebrow">{agent ? 'Building' : 'Agent Builder'}</span>
        <h1>{agent ? agent.name || agent.id : 'What should we build?'}</h1>
        <p className="head-sub">
          {agent
            ? [`agents/${agent.id}/`, agent.version && `v${agent.version}`].filter(Boolean).join('  ·  ')
            : 'Describe an agent and I will write it, check it, and make it shippable.'}
        </p>
      </div>

      <div className="topbar-actions">
        {/* The note doubles as the status: "Restarting…" while it happens, the refusal if it is
            refused. A button that changes label and says nothing else leaves you watching a
            window that went quiet. */}
        {restartNote && <span className="restart-note">{restartNote}</span>}
        <button
          className="ghost-btn"
          onClick={onRestart}
          disabled={restarting}
          title="Restart agentd — needed after editing a plugin's Python, which is already imported"
        >
          {restarting ? 'Restarting…' : 'Restart'}
        </button>
        {/* WHO this window is connected as — the identity a Publish would be signed with. Hidden
            entirely when the daemon is too old to answer; "not signed in" only when it SAID so. */}
        {who.known && (
          <span className={`whoami ${who.signedIn ? '' : 'anon'}`} title={who.title}>
            <span className="who-dot" />
            {who.label}
          </span>
        )}
        {canTogglePanel && (
          <button
            className={`icon-btn ${panelOpen ? 'on' : ''}`}
            onClick={onTogglePanel}
            title={panelOpen ? 'Hide inspector' : 'Show inspector'}
          >
            ▤
          </button>
        )}
      </div>
    </header>
  )
}
