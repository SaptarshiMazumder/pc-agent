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
}: {
  agent: AgentRow | null
  who: WhoAmI
  canTogglePanel: boolean
  panelOpen: boolean
  onTogglePanel: () => void
}) {
  return (
    <header className="topbar">
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
