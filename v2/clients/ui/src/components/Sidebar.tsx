import { useState, type ReactNode } from 'react'
import {
  Plus,
  Search,
  Users,
  Folder,
  History,
  PanelLeft,
  ShoppingBag,
  Sun,
  Moon,
  SquarePen,
  ChevronDown,
  ChevronRight,
  UserPlus
} from 'lucide-react'

import logo from '../assets/nakama.svg'
import { agentColor, agentInitials, agentTag, MAIN_AGENT_ID } from '../lib/agentPresentation'
import { useApp } from '../state/store'
import NewAgentModal from './NewAgentModal'
import ProfileMenu from './ProfileMenu'
import SearchBox from './SearchBox'
import SessionItem from './SessionItem'
import SettingsMenu from './SettingsMenu'

/** A collapsible section header (Agents / Recents). Clicking the label toggles open/closed;
 *  the caret + optional "+" action appear on hover. */
function SectionHead({
  icon,
  label,
  open,
  onToggle,
  onAdd,
  addTitle,
  extraClass
}: {
  icon: ReactNode
  label: string
  open: boolean
  onToggle: () => void
  onAdd?: () => void
  addTitle?: string
  extraClass?: string
}) {
  return (
    <div
      className={`section-label section-head ${extraClass ?? ''}`}
      onClick={onToggle}
      title={`${open ? 'collapse' : 'expand'} ${label.toLowerCase()}`}
    >
      {icon}
      <span className="section-title">{label}</span>
      {onAdd && (
        <button className="section-add" title={addTitle} onClick={(e) => { e.stopPropagation(); onAdd() }}>
          <Plus size={14} />
        </button>
      )}
      <span className="section-caret">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
    </div>
  )
}

/** A compact ChatGPT-style nav row (icon + label), used for New chat / Search / Projects. */
function NavRow({
  icon,
  label,
  active,
  onClick,
  title
}: {
  icon: ReactNode
  label: string
  active?: boolean
  onClick: () => void
  title?: string
}) {
  return (
    <button className={`nav-row ${active ? 'active' : ''}`} onClick={onClick} title={title || label}>
      {icon}
      <span className="nav-row-label">{label}</span>
    </button>
  )
}

export default function Sidebar() {
  const flavor = useApp((s) => s.flavor)
  const agents = useApp((s) => s.agents)
  const currentAgentId = useApp((s) => s.currentAgentId)
  const viewedAgentId = useApp((s) => s.viewedAgentId)
  const viewAgent = useApp((s) => s.viewAgent)
  const newChat = useApp((s) => s.newChat)
  const recents = useApp((s) => s.recents)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const resumeSession = useApp((s) => s.resumeSession)
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const connection = useApp((s) => s.connection)
  const theme = useApp((s) => s.theme)
  const toggleTheme = useApp((s) => s.toggleTheme)
  const collapsed = useApp((s) => s.sidebarCollapsed)
  const toggleSidebar = useApp((s) => s.toggleSidebar)

  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [sectionOpen, setSectionOpen] = useState({ agents: true, recents: true })
  const toggleSection = (k: 'agents' | 'recents'): void =>
    setSectionOpen((s) => ({ ...s, [k]: !s[k] }))
  const [newAgent, setNewAgent] = useState(false)

  const q = query.trim().toLowerCase()
  const chats = recents.filter((s) => !q || (s.title || s.sessionId).toLowerCase().includes(q))
  // 'main' is the DEFAULT agent (what a plain New chat talks to) — hide it from the roster so
  // the list shows only the named agents you created. It's still the default everywhere else.
  const namedAgents = agents.filter((a) => a.id !== MAIN_AGENT_ID)

  const projectsActive = view === 'projects' || view === 'project'

  // ---- collapsed icon rail --------------------------------------------------
  if (collapsed) {
    return (
      <aside className="sidebar sidebar--rail">
        <img className="brand-logo brand-logo--rail" src={logo} alt="" />
        <button className="rail-btn" title="expand sidebar" onClick={toggleSidebar}><PanelLeft size={17} /></button>
        <button className="rail-primary" title="new chat" onClick={() => newChat()}><SquarePen size={17} /></button>
        <button className="rail-btn" title="search chats" onClick={toggleSidebar}><Search size={17} /></button>
        <button className={`rail-btn ${projectsActive ? 'active' : ''}`} title="Projects" onClick={() => setView('projects')}><Folder size={17} /></button>
        <div className="rail-sep" />
        <button className="rail-btn" title="create agent" onClick={() => setNewAgent(true)}><UserPlus size={17} /></button>
        {namedAgents.map((a) => (
          <button
            key={a.id}
            className={`rail-agent ${a.id === viewedAgentId && view === 'agent' ? 'active' : ''}`}
            title={a.name || a.id}
            onClick={() => viewAgent(a.id)}
          >
            <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(a.name, a.id)}</span>
          </button>
        ))}
        <div className="rail-spacer" />
        <ProfileMenu variant="rail" />
        <button className="rail-btn" title="Marketplace" onClick={() => setView('marketplace')}><ShoppingBag size={18} /></button>
        <SettingsMenu variant="rail" />
        <button className="rail-btn" title="toggle theme" onClick={toggleTheme}>{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}</button>
        {newAgent && <NewAgentModal onClose={() => setNewAgent(false)} />}
      </aside>
    )
  }

  // ---- full sidebar ---------------------------------------------------------
  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-logo" src={logo} alt="" />
        <span className="brand-name">{flavor?.productName || 'agentd'}</span>
        <span className="live"><span className="live-dot" style={{ opacity: connection === 'open' ? 1 : 0.3 }} />live</span>
        <button className="icon-btn icon-btn--sm" title="collapse sidebar" onClick={toggleSidebar}><PanelLeft size={17} /></button>
      </div>

      {/* compact nav rows */}
      <div className="nav-rows">
        <NavRow icon={<SquarePen size={17} />} label="New chat" onClick={() => newChat()} title="New chat" />
        {searchOpen ? (
          <SearchBox
            className="search nav-search"
            value={query}
            onChange={setQuery}
            placeholder="Search chats"
            iconSize={16}
            autoFocus
            onBlur={() => { if (!query.trim()) setSearchOpen(false) }}
            onKeyDown={(e) => { if (e.key === 'Escape') { setQuery(''); setSearchOpen(false) } }}
          />
        ) : (
          <NavRow icon={<Search size={17} />} label="Search" onClick={() => setSearchOpen(true)} title="Search chats" />
        )}
        <NavRow icon={<Folder size={17} />} label="Projects" active={projectsActive} onClick={() => setView('projects')} title="Projects" />
      </div>

      {/* AGENTS — the listing STAYS; clicking an agent opens its detail page (not a chat) */}
      <SectionHead
        icon={<Users size={14} />}
        label="Agents"
        open={sectionOpen.agents}
        onToggle={() => toggleSection('agents')}
        onAdd={() => setNewAgent(true)}
        addTitle="create agent"
      />
      {sectionOpen.agents && (
        <div className="agents-list">
          {namedAgents.length === 0 && (
            <div className="row-sub list-empty">no agents yet — use +</div>
          )}
          {namedAgents.map((a) => (
            <button
              key={a.id}
              className={`row ${a.id === viewedAgentId && view === 'agent' ? 'active' : ''}`}
              title={a.name || a.id}
              onClick={() => viewAgent(a.id)}
            >
              <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(a.name, a.id)}</span>
              <span className="row-main">
                <span className="row-title">{a.name || a.id}</span>
                <span className="row-sub">{a.tagline || agentTag(a.id)}</span>
              </span>
            </button>
          ))}
        </div>
      )}

      {/* RECENTS — ALL chats across every agent (agent dot + project badge) */}
      <div className="sidebar-scroll">
        <SectionHead
          icon={<History size={14} />}
          label="Recents"
          open={sectionOpen.recents}
          onToggle={() => toggleSection('recents')}
          extraClass="section-chats"
        />
        {sectionOpen.recents && (<>
          {chats.slice(0, 100).map((s) => (
            <SessionItem
              key={s.sessionId}
              session={s}
              active={view === 'chat' && s.sessionId === currentSessionKey}
              onOpen={() => void resumeSession(s.sessionId)}
              withAgentDot
              withProjectBadge
            />
          ))}
          {chats.length === 0 && (
            <div className="row-sub list-empty">
              {q ? 'no chats match' : 'no saved chats yet'}
            </div>
          )}
        </>)}
      </div>

      <div className="footer-nav">
        <ProfileMenu variant="footer" />
        <button className={`icon-btn footer-icon push-end ${view === 'marketplace' ? 'active' : ''}`} title="Marketplace" onClick={() => setView('marketplace')}>
          <ShoppingBag size={17} />
        </button>
        <SettingsMenu variant="footer" />
        <button className="icon-btn footer-icon" title="toggle theme" onClick={toggleTheme}>
          {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </div>

      {newAgent && <NewAgentModal onClose={() => setNewAgent(false)} />}
    </aside>
  )
}
