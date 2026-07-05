import { useEffect, useRef, useState } from 'react'

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
  const renameSession = useApp((state) => state.renameSession)
  const newSession = useApp((state) => state.newSession)
  const view = useApp((state) => state.view)
  const setView = useApp((state) => state.setView)
  const connection = useApp((state) => state.connection)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const editRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId) editRef.current?.select()
  }, [editingId])

  function beginEdit(sessionId: string, current: string) {
    setEditingId(sessionId)
    setDraftTitle(current)
  }

  function commitEdit() {
    if (editingId) void renameSession(editingId, draftTitle.trim())
    setEditingId(null)
  }

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
        {sessionRows.slice(0, 20).map((session) => {
          const isEditing = editingId === session.sessionId
          const label = session.title || session.sessionId
          return (
            <div
              key={session.sessionId}
              className={`row session-row ${session.sessionId === currentSessionKey ? 'row-active' : ''}`}
              onClick={() => { if (!isEditing) { setView('chat'); void resumeSession(session.sessionId) } }}
              onDoubleClick={() => beginEdit(session.sessionId, label)}
              title="double-click to rename"
            >
              {isEditing ? (
                <input
                  ref={editRef}
                  className="rename-input"
                  value={draftTitle}
                  autoFocus
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onBlur={commitEdit}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); commitEdit() }
                    else if (e.key === 'Escape') { e.preventDefault(); setEditingId(null) }
                  }}
                />
              ) : (
                <>
                  <div className="session-row-main">
                    <span className="row-title">{label}</span>
                    <span
                      className="rename-btn"
                      title="rename"
                      onClick={(e) => { e.stopPropagation(); beginEdit(session.sessionId, label) }}
                    >
                      ✎
                    </span>
                  </div>
                  <span className="row-sub">{session.messages} messages</span>
                </>
              )}
            </div>
          )
        })}
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
