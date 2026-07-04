import { useApp } from '../state/store'

export default function Sidebar() {
  const flavor = useApp((state) => state.flavor)
  const hello = useApp((state) => state.hello)
  const agents = useApp((state) => state.agents)
  const currentAgentId = useApp((state) => state.currentAgentId)
  const selectAgent = useApp((state) => state.selectAgent)
  const sessionRows = useApp((state) => state.sessionRows)
  const currentSessionKey = useApp((state) => state.currentSessionKey)
  const resumeSession = useApp((state) => state.resumeSession)
  const newSession = useApp((state) => state.newSession)
  const view = useApp((state) => state.view)
  const setView = useApp((state) => state.setView)
  const connection = useApp((state) => state.connection)

  const storeEnabled = (hello?.storeEnabled ?? true) && (flavor?.storeEnabled ?? true)

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className={`dot ${connection === 'open' ? 'dot-ok' : 'dot-off'}`} />
        <span className="brand-name">{flavor?.productName || 'agentd'}</span>
      </div>

      <button className="button primary new-chat" onClick={() => { setView('chat'); newSession() }}>
        + New chat
      </button>

      <div className="section-label">Agents</div>
      <div className="agent-list">
        {agents.map((agent) => (
          <button
            key={agent.id}
            className={`row ${agent.id === currentAgentId ? 'row-active' : ''}`}
            onClick={() => { setView('chat'); void selectAgent(agent.id) }}
          >
            <span className="row-title">{agent.name || agent.id}</span>
            <span className="row-sub">{agent.id}</span>
          </button>
        ))}
      </div>

      <div className="section-label">Recent sessions</div>
      <div className="session-list">
        {sessionRows.slice(0, 12).map((session) => (
          <button
            key={session.sessionId}
            className={`row ${session.sessionId === currentSessionKey ? 'row-active' : ''}`}
            onClick={() => { setView('chat'); resumeSession(session.sessionId) }}
          >
            <span className="row-title mono">{session.sessionId}</span>
            <span className="row-sub">{session.messages} messages</span>
          </button>
        ))}
        {sessionRows.length === 0 && <div className="row-sub pad">no saved sessions yet</div>}
      </div>

      <div className="sidebar-footer">
        {storeEnabled && (
          <button className={`nav ${view === 'store' ? 'nav-active' : ''}`} onClick={() => setView('store')}>
            ⬇ Store
          </button>
        )}
        <button className={`nav ${view === 'settings' ? 'nav-active' : ''}`} onClick={() => setView('settings')}>
          ⚙ Settings
        </button>
      </div>
    </aside>
  )
}
