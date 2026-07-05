import { useState } from 'react'
import { Plus, ChevronDown, X } from 'lucide-react'

import { agentColor } from '../lib/agentPresentation'
import { useApp } from '../state/store'

/**
 * Chrome-style tab strip for the open chats.
 *  - horizontal scroll (scrollbar hidden via .tabbar-scroll::-webkit-scrollbar)
 *  - "+" opens a new chat, the chevron opens an overflow list of all open chats
 *  - tabs are draggable to reorder
 * Each tab carries its own agentId (dot colour + routing) and titles come from
 * the cross-agent tabTitles cache — so switching agents never blanks tab names.
 */
export default function TabBar() {
  const openTabs = useApp((s) => s.openTabs)
  const current = useApp((s) => s.currentSessionKey)
  const tabTitles = useApp((s) => s.tabTitles)
  const agents = useApp((s) => s.agents)
  const activateTab = useApp((s) => s.activateTab)
  const newSession = useApp((s) => s.newSession)
  const closeTab = useApp((s) => s.closeTab)
  const reorderTabs = useApp((s) => s.reorderTabs)

  const [menuOpen, setMenuOpen] = useState(false)
  const dragged = useState<{ id: string | null }>({ id: null })[0]

  if (openTabs.length === 0) return null

  const titleOf = (id: string): string => tabTitles[id] || 'New chat'
  // dot colour = the tab's own agent colour (server-assigned, falls back to a hash)
  const dotOf = (agentId: string): string =>
    agentColor(agents.find((a) => a.id === agentId)?.color, agentId)

  return (
    <div className="tabbar">
      <div className="tabbar-clip">
        <div className="tabbar-scroll">
          {openTabs.map((tab) => (
            <div
              key={tab.id}
              className={`tab ${tab.id === current ? 'active' : ''}`}
              onClick={() => void activateTab(tab)}
              draggable
              onDragStart={() => {
                dragged.id = tab.id
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                if (dragged.id) reorderTabs(dragged.id, tab.id)
                dragged.id = null
              }}
            >
              <span className="tab-dot" style={{ background: dotOf(tab.agentId) }} />
              <span className="tab-title">{titleOf(tab.id)}</span>
              <button
                className="tab-close"
                title="close tab"
                onClick={(e) => {
                  e.stopPropagation()
                  closeTab(tab.id)
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
            {openTabs.map((tab) => (
              <button
                key={tab.id}
                className="tab-menu-item"
                onClick={() => {
                  void activateTab(tab)
                  setMenuOpen(false)
                }}
              >
                <span className="tab-dot" style={{ background: dotOf(tab.agentId) }} />
                <span className="tab-title">{titleOf(tab.id)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
