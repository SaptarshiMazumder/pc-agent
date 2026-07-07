import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  Plus,
  Search,
  Users,
  Folder,
  History,
  PanelLeft,
  ShoppingBag,
  Monitor,
  Sun,
  Moon,
  Pencil,
  X,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  UserPlus
} from 'lucide-react'

import logo from '../assets/nakama.svg'
import type { SessionRow } from '../gateway/protocol'
import { agentColor, agentInitials, agentTag, hashColor } from '../lib/agentPresentation'
import { whenLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import ChatMenu from './ChatMenu'
import { useHoverTip } from './HoverTip'
import NewAgentModal from './NewAgentModal'
import SettingsMenu from './SettingsMenu'

/** One saved-chat row: hover tooltip (full name + meta), double-click to rename, and a ⋯
 *  menu (rename · move to project · export md · duplicate · delete) revealed on hover. */
function SessionItem({ session, active, onOpen }: { session: SessionRow; active: boolean; onOpen: () => void }) {
  const renameSession = useApp((s) => s.renameSession)
  const deleteSession = useApp((s) => s.deleteSession)
  const moveSession = useApp((s) => s.moveSession)
  const duplicateSession = useApp((s) => s.duplicateSession)
  const exportSessionMd = useApp((s) => s.exportSessionMd)
  const projects = useApp((s) => s.projects)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [menu, setMenu] = useState<DOMRect | null>(null) // ⋯ menu anchor (open when set)
  const tip = useHoverTip()
  const ref = useRef<HTMLInputElement>(null)
  const label = session.title || session.sessionId
  const meta = `${session.messages} msgs · ${whenLabel(session.modified * 1000)}`

  useEffect(() => {
    if (editing) ref.current?.select()
  }, [editing])

  function commit() {
    void renameSession(session.sessionId, draft.trim())
    setEditing(false)
  }

  if (editing) {
    return (
      <input
        ref={ref}
        className="rename-input"
        value={draft}
        autoFocus
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit() }
          else if (e.key === 'Escape') { e.preventDefault(); setEditing(false) }
        }}
      />
    )
  }

  return (
    <button
      className={`row session-row ${active ? 'active' : ''} ${menu ? 'menu-open' : ''}`}
      onClick={onOpen}
      onDoubleClick={() => { setDraft(label); setEditing(true) }}
      {...(menu ? {} : tip.bind(label, meta))}
    >
      <span className="row-main">
        <span className="row-title">{label}</span>
      </span>
      <span className="row-actions">
        <span
          className="hover-btn"
          title="more"
          onClick={(e) => { e.stopPropagation(); tip.hide(); setMenu(e.currentTarget.getBoundingClientRect()) }}
        >
          <MoreHorizontal size={15} />
        </span>
      </span>
      {tip.node}
      {menu && (
        <ChatMenu
          anchor={menu}
          projects={projects}
          currentProjectId={session.projectId}
          onClose={() => setMenu(null)}
          onRename={() => { setDraft(label); setEditing(true) }}
          onDelete={() => void deleteSession(session.sessionId)}
          onDuplicate={() => void duplicateSession(session.sessionId)}
          onExport={() => void exportSessionMd(session.sessionId)}
          onMove={(pid) => void moveSession(session.sessionId, pid)}
        />
      )}
    </button>
  )
}

/**
 * A collapsible section header (Agents / Projects / Chats). Clicking anywhere on the label
 * toggles the section open/closed (ChatGPT-style); the caret + the optional "+" action only
 * appear on hover. The caret is a plain span so its clicks bubble up to the header toggle.
 */
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
        <button
          className="section-add"
          title={addTitle}
          onClick={(e) => {
            e.stopPropagation()
            onAdd()
          }}
        >
          <Plus size={14} />
        </button>
      )}
      <span className="section-caret">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
    </div>
  )
}

export default function Sidebar() {
  const flavor = useApp((s) => s.flavor)
  const agents = useApp((s) => s.agents)
  const currentAgentId = useApp((s) => s.currentAgentId)
  const selectAgent = useApp((s) => s.selectAgent)
  const sessionRows = useApp((s) => s.sessionRows)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const resumeSession = useApp((s) => s.resumeSession)
  const newSession = useApp((s) => s.newSession)
  const projects = useApp((s) => s.projects)
  const createProject = useApp((s) => s.createProject)
  const renameProject = useApp((s) => s.renameProject)
  const deleteProject = useApp((s) => s.deleteProject)
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const connection = useApp((s) => s.connection)
  const theme = useApp((s) => s.theme)
  const toggleTheme = useApp((s) => s.toggleTheme)
  const collapsed = useApp((s) => s.sidebarCollapsed)
  const toggleSidebar = useApp((s) => s.toggleSidebar)

  const [query, setQuery] = useState('')
  // which top-level sections (Agents / Projects / Chats) are expanded — collapse toggles
  const [sectionOpen, setSectionOpen] = useState({ agents: true, projects: true, chats: true })
  const toggleSection = (k: 'agents' | 'projects' | 'chats'): void =>
    setSectionOpen((s) => ({ ...s, [k]: !s[k] }))
  const [openProjects, setOpenProjects] = useState<Record<string, boolean>>({})
  const [newAgent, setNewAgent] = useState(false)
  const [addingProject, setAddingProject] = useState(false)
  const [projectDraft, setProjectDraft] = useState('')
  const [renamingProject, setRenamingProject] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [armedProject, setArmedProject] = useState<string | null>(null)

  useEffect(() => {
    if (!armedProject) return
    const t = setTimeout(() => setArmedProject(null), 3000)
    return () => clearTimeout(t)
  }, [armedProject])

  function submitNewProject() {
    const name = projectDraft.trim()
    setAddingProject(false)
    setProjectDraft('')
    if (name) void createProject(name)
  }

  const projectIds = new Set(projects.map((p) => p.id))
  const q = query.trim().toLowerCase()
  const match = (t: string) => !q || t.toLowerCase().includes(q)
  const standalone = sessionRows.filter((s) => (!s.projectId || !projectIds.has(s.projectId)) && match(s.title || s.sessionId))
  const byProject = new Map<string, SessionRow[]>()
  for (const s of sessionRows) {
    if (s.projectId && projectIds.has(s.projectId) && match(s.title || s.sessionId)) {
      byProject.set(s.projectId, [...(byProject.get(s.projectId) || []), s])
    }
  }

  // ---- collapsed icon rail --------------------------------------------------
  if (collapsed) {
    return (
      <aside className="sidebar sidebar--rail">
        <img className="brand-logo" src={logo} alt="" style={{ width: 34, height: 34, marginBottom: 4 }} />
        <button className="rail-btn" title="expand sidebar" onClick={toggleSidebar}><PanelLeft size={17} /></button>
        <button className="rail-primary" title="new chat" onClick={() => newSession()}><Plus size={17} /></button>
        <button className="rail-btn" title="search chats" onClick={toggleSidebar}><Search size={17} /></button>
        <div className="rail-sep" />
        <button className="rail-btn" title="create agent" onClick={() => setNewAgent(true)}><UserPlus size={17} /></button>
        {agents.map((a) => (
          <button
            key={a.id}
            className={`rail-agent ${a.id === currentAgentId && view === 'chat' ? 'active' : ''}`}
            title={a.name || a.id}
            onClick={() => void selectAgent(a.id)}
          >
            <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(a.name, a.id)}</span>
          </button>
        ))}
        <div className="rail-spacer" />
        <button className="rail-btn" title="local profile"><Monitor size={18} /></button>
        <button className="rail-btn" title="Store" onClick={() => setView('store')}><ShoppingBag size={18} /></button>
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
        <button className="icon-btn" style={{ width: 30, height: 30 }} title="collapse sidebar" onClick={toggleSidebar}><PanelLeft size={17} /></button>
      </div>

      <div style={{ padding: '0 12px 4px' }}>
        <button className="new-chat" onClick={() => newSession()}><Plus size={17} />New chat</button>
      </div>
      <div className="search">
        <Search size={16} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" />
      </div>

      {/* AGENTS — own fixed-height region, scrolls independently of chats */}
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
          {agents.map((a) => (
            <button
              key={a.id}
              className={`row ${a.id === currentAgentId && view === 'chat' ? 'active' : ''}`}
              onClick={() => void selectAgent(a.id)}
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

      {/* PROJECTS + CHATS — the scrolling remainder */}
      <div className="sidebar-scroll">
        <SectionHead
          icon={<Folder size={14} />}
          label="Projects"
          open={sectionOpen.projects}
          onToggle={() => toggleSection('projects')}
          onAdd={() => { setSectionOpen((s) => ({ ...s, projects: true })); setAddingProject(true); setProjectDraft('') }}
          addTitle="new project"
          extraClass="section-projects"
        />
        {sectionOpen.projects && (<>
        {addingProject && (
          <input
            className="rename-input"
            placeholder="project name…"
            value={projectDraft}
            autoFocus
            onChange={(e) => setProjectDraft(e.target.value)}
            onBlur={submitNewProject}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); submitNewProject() }
              else if (e.key === 'Escape') { e.preventDefault(); setAddingProject(false) }
            }}
          />
        )}
        {projects.map((p) => {
          const chats = byProject.get(p.id) || []
          const open = openProjects[p.id] ?? true
          const isRenaming = renamingProject === p.id
          const isArmed = armedProject === p.id
          return (
            <div key={p.id}>
              {isRenaming ? (
                <input
                  className="rename-input"
                  value={renameDraft}
                  autoFocus
                  onChange={(e) => setRenameDraft(e.target.value)}
                  onBlur={() => { if (renameDraft.trim()) void renameProject(p.id, renameDraft.trim()); setRenamingProject(null) }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); if (renameDraft.trim()) void renameProject(p.id, renameDraft.trim()); setRenamingProject(null) }
                    else if (e.key === 'Escape') { e.preventDefault(); setRenamingProject(null) }
                  }}
                />
              ) : (
                <div className="row project-row" onClick={() => setOpenProjects((c) => ({ ...c, [p.id]: !open }))}>
                  <span className="proj-caret">{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
                  <span style={{ display: 'flex', color: hashColor(p.id) }}><Folder size={15} /></span>
                  <span className="row-title" style={{ flex: 1 }}>{p.name}</span>
                  <button className="hover-btn" title="new chat in this project"
                    onClick={(e) => { e.stopPropagation(); newSession(p.id) }}><Plus size={13} /></button>
                  <button className="hover-btn" title="rename project"
                    onClick={(e) => { e.stopPropagation(); setRenameDraft(p.name); setRenamingProject(p.id) }}><Pencil size={13} /></button>
                  <button className={`hover-btn ${isArmed ? 'danger' : ''}`}
                    title={isArmed ? 'click again to delete (chats become standalone)' : 'delete project'}
                    onClick={(e) => { e.stopPropagation(); isArmed ? void deleteProject(p.id) : setArmedProject(p.id) }}>
                    {isArmed ? <span style={{ fontSize: 11, fontWeight: 600 }}>sure?</span> : <X size={13} />}
                  </button>
                </div>
              )}
              {open && !isRenaming && (
                <div className="project-sessions">
                  {chats.map((s) => (
                    <SessionItem key={s.sessionId} session={s} active={s.sessionId === currentSessionKey} onOpen={() => void resumeSession(s.sessionId)} />
                  ))}
                  {chats.length === 0 && <div className="row-sub" style={{ padding: '3px 9px' }}>no chats yet — use +</div>}
                </div>
              )}
            </div>
          )
        })}
        </>)}
        <SectionHead
          icon={<History size={14} />}
          label="Chats"
          open={sectionOpen.chats}
          onToggle={() => toggleSection('chats')}
          extraClass="section-chats"
        />
        {sectionOpen.chats && (<>
        {standalone.slice(0, 40).map((s) => (
          <SessionItem key={s.sessionId} session={s} active={s.sessionId === currentSessionKey} onOpen={() => void resumeSession(s.sessionId)} />
        ))}
        {standalone.length === 0 && <div className="row-sub" style={{ padding: '4px 9px' }}>no saved chats yet</div>}
        </>)}
      </div>

      <div className="footer-nav">
        <button className="icon-btn footer-icon" title="local profile"><Monitor size={17} /></button>
        <button className={`icon-btn footer-icon ${view === 'store' ? 'active' : ''}`} style={{ marginLeft: 'auto' }} title="Store" onClick={() => setView('store')}>
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
