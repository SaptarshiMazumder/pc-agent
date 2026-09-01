/* The subject header: WHO this conversation is about, worn like a masthead — avatar, name, and
 * the one line of mono truth (path · version). The old two-line eyebrow ("Building / name") said
 * the same thing in words; the avatar tile now says it at a glance and the path says it exactly.
 *
 * It holds only what is about this view. Actions live where their subject is — the ones that act
 * on an agent are in the inspector, next to that agent's files. The workspace tab strip and the
 * Ship button land HERE in later slices, each arriving with the screen it switches to.
 */

import { ArrowUp, FileText, Monitor, PanelRight, Play, SlidersHorizontal } from 'lucide-react'

import type { WhoAmI } from '../agentd/platform'
import type { AgentRow } from '../agentd/roster'
import { agentColor, agentInitials } from '../lib/agentPresentation'

export function Topbar({
  agent,
  who,
  canTogglePanel,
  panelOpen,
  onTogglePanel,
  onRestart,
  restarting,
  restartNote,
  previewable = false,
  wsTab = '',
  onWsTab,
  onShip,
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
  /** Does the subject have a window to preview? ABSENT strip otherwise — a Preview tab for an
   *  agent with no window is a button that can only disappoint. */
  previewable?: boolean
  /** The current conversation's workspace pane; clicking the active tab closes it back to the
   *  full-width chat, so the strip is also the way out. Capabilities / Test drive join this
   *  strip with the slices that build their screens. */
  wsTab?: '' | 'preview' | 'files' | 'caps' | 'test'
  onWsTab?: (tab: '' | 'preview' | 'files' | 'caps' | 'test') => void
  /** Open the Ship screen — preflight, package, publish. Rendered only with a subject. */
  onShip?: () => void
}) {
  const toggle = (tab: 'preview' | 'files' | 'caps' | 'test'): void => onWsTab?.(wsTab === tab ? '' : tab)
  return (
    <header className="topbar">
      {/* THE WAY BACK USED TO LIVE HERE. Collapsing the sidebar removed it entirely, taking its
          own expand control with it, so this button existed to undo that. The sidebar now
          collapses to agentd's 64px icon rail instead of disappearing — the expand control is
          always on screen — and a second door to it would be a button that is never needed. */}
      {agent && (
        <span className="subj-avatar" style={{ background: agentColor(agent.color, agent.id) }}>
          {agentInitials(agent.name, agent.id)}
        </span>
      )}
      <div className="subj-text">
        <h1 className="subj-name">{agent ? agent.name || agent.id : 'What should we build?'}</h1>
        <p className="subj-sub">
          {agent
            ? [`agents/${agent.id}/`, agent.version && `v${agent.version}`].filter(Boolean).join(' · ')
            : 'Describe an agent and I will write it, check it, and make it shippable.'}
        </p>
      </div>

      {agent && onWsTab && (
        <div className="ws-tabs" role="tablist" aria-label="Workspace panes">
          {/* Preview only when there is a window to run; Files whenever there is an agent —
              every agent has source, only some have a screen. */}
          {previewable && (
            <button
              className={`ws-tab ${wsTab === 'preview' ? 'active' : ''}`}
              role="tab"
              aria-selected={wsTab === 'preview'}
              title={wsTab === 'preview' ? 'Close the preview' : 'Run the window beside the chat'}
              onClick={() => toggle('preview')}
            >
              <Monitor size={14} />
              Preview
            </button>
          )}
          <button
            className={`ws-tab ${wsTab === 'files' ? 'active' : ''}`}
            role="tab"
            aria-selected={wsTab === 'files'}
            title={wsTab === 'files' ? 'Close the source pane' : 'Read the agent’s source beside the chat'}
            onClick={() => toggle('files')}
          >
            <FileText size={14} />
            Files
          </button>
          <button
            className={`ws-tab ${wsTab === 'caps' ? 'active' : ''}`}
            role="tab"
            aria-selected={wsTab === 'caps'}
            title={wsTab === 'caps' ? 'Close the capabilities pane' : 'What this agent may reach, from its agent.toml'}
            onClick={() => toggle('caps')}
          >
            <SlidersHorizontal size={14} />
            Capabilities
          </button>
          <button
            className={`ws-tab ${wsTab === 'test' ? 'active' : ''}`}
            role="tab"
            aria-selected={wsTab === 'test'}
            title={wsTab === 'test' ? 'Close the test-drive pane' : 'Try the agent as a user would'}
            onClick={() => toggle('test')}
          >
            <Play size={14} />
            Test drive
          </button>
        </div>
      )}

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
        {agent && onShip && (
          <button className="prime-btn" onClick={onShip} title="Preflight, package and publish">
            <ArrowUp size={15} />
            Ship
          </button>
        )}
        {canTogglePanel && (
          <button
            className={`icon-btn panel-toggle ${panelOpen ? 'on' : ''}`}
            onClick={onTogglePanel}
            title={panelOpen ? 'Hide inspector' : 'Show inspector'}
            aria-label={panelOpen ? 'Hide inspector' : 'Show inspector'}
          >
            <PanelRight size={16} />
          </button>
        )}
      </div>
    </header>
  )
}
