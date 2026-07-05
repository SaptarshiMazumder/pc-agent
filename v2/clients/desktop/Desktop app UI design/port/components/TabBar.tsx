import { useState } from 'react'
import { Plus, ChevronDown, X } from 'lucide-react'

import { agentColor } from '../lib/agentPresentation'
import { useApp } from '../state/store'

/**
 * Chrome-style tab strip for the open chats.
 *  - horizontal scroll (scrollbar hidden via .tabbar-scroll::-webkit-scrollbar)
 *  - "+" opens a new chat, the chevron opens an overflow list of all open chats
 *  - tabs are draggable to reorder
 * Reads: openTabs, currentSessionKey, currentAgentId, sessionRows.
 * Actions: resumeSession, closeTab, reorderTabs, newSession.
 */
export default function TabBar() {
  const openTabs = useApp((s) => s.openTabs)
  const current = useApp((s) => s.currentSessionKey)
  const agentId = useApp((s) => s.currentAgentId)
  const sessionRows = useApp((s) => s.sessionRows)
  const resumeSession = useApp((s) => s.resumeSession)
  const newSession = useApp((s) => s.newSession)
  const closeTab = useApp((s) => s.closeTab)
  const reorderTabs = useApp((s) => s.reorderTabs)

  const [menuOpen, setMenuOpen] = useState(false)
  const dragged = useState<{ id: string | null }>({ id: null })[0]

  if (openTabs.length === 0) return null

  const titleOf = (id: string): string =>
    sessionRows.find((r) => r.sessionId === id)?.title || 'New chat'
  const dot = agentColor(agentId)

  return (
    <div className="tabbar">
      <div className="tabbar-clip">
        <div className="tabbar-scroll">
          {openTabs.map((id) => (
            <div
              key={id}
              className={`tab ${id === current ? 'active' : ''}`}
              onClick={() => void resumeSession(id)}
              draggable
              onDragStart={() => {
                dragged.id = id
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                if (dragged.id) reorderTabs(dragged.id, id)
                dragged.id = null
              }}
            >
              <span className="tab-dot" style={{ background: dot }} />
              <span className="tab-title">{titleOf(id)}</span>
              <button
                className="tab-close"
                title="close tab"
                onClick={(e) => {
                  e.stopPropagation()
                  closeTab(id)
                }}
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>

      <button className="tabbar-add" title="new chat" onClick={() => newSession()}>
        <Plus size={16} />
      </button>

      <div className="tabbar-menu-wrap">
        <button className="tabbar-add" title="all open chats" onClick={() => setMenuOpen((v) => !v)}>
          <ChevronDown size={16} />
        </button>
        {menuOpen && (
          <div className="tab-menu">
            <div className="tab-menu-label">Open chats</div>
            {openTabs.map((id) => (
              <button
                key={id}
                className="tab-menu-item"
                onClick={() => {
                  void resumeSession(id)
                  setMenuOpen(false)
                }}
              >
                <span className="tab-dot" style={{ background: dot }} />
                <span className="tab-title">{titleOf(id)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
