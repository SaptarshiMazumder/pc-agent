import { KeyboardEvent, useEffect, useRef, useState } from 'react'

import logo from '../assets/nakama.svg'
import type { SessionRow } from '../gateway/protocol'
import { whenLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import {
  IconChat,
  IconChevronDown,
  IconChevronRight,
  IconDownload,
  IconFolder,
  IconMoon,
  IconPencil,
  IconPlus,
  IconSliders,
  IconSparkle,
  IconSun,
  IconX
} from './icons'

/** One saved-conversation row: title (server data), WhatsApp-style 'when', and
 *  hover actions — rename (✎ / double-click) and delete (✕, two-step confirm). */
function SessionItem({
  session,
  active,
  onOpen
}: {
  session: SessionRow
  active: boolean
  onOpen: () => void
}) {
  const renameSession = useApp((state) => state.renameSession)
  const deleteSession = useApp((state) => state.deleteSession)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [armed, setArmed] = useState(false) // first delete click arms; second deletes
  const editRef = useRef<HTMLInputElement>(null)
  const label = session.title || session.sessionId

  useEffect(() => {
    if (editing) editRef.current?.select()
  }, [editing])

  useEffect(() => {
    if (!armed) return
    const timer = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(timer)
  }, [armed])

  function commit() {
    void renameSession(session.sessionId, draft.trim())
    setEditing(false)
  }

  function rowKey(e: KeyboardEvent) {
    if (!editing && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      onOpen()
    }
  }

  return (
    <div
      className={`row session-row ${active ? 'row-active' : ''}`}
      role="button"
      tabIndex={0}
      onKeyDown={rowKey}
      onClick={() => {
        if (!editing) onOpen()
      }}
      onDoubleClick={() => {
        setDraft(label)
        setEditing(true)
      }}
      title="double-click to rename"
    >
      {editing ? (
        <input
          ref={editRef}
          className="rename-input"
          value={draft}
          autoFocus
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setEditing(false)
            }
          }}
        />
      ) : (
        <>
          <div className="session-row-main">
            <span className="row-title">{label}</span>
            <button
              type="button"
              className="hover-btn"
              title="rename"
              aria-label={`rename ${label}`}
              onClick={(e) => {
                e.stopPropagation()
                setDraft(label)
                setEditing(true)
              }}
            >
              <IconPencil size={13} />
            </button>
            <button
              type="button"
              className={`hover-btn ${armed ? 'hover-btn-danger' : ''}`}
              title={armed ? 'click again to delete — permanent' : 'delete chat'}
              aria-label={armed ? `confirm delete ${label}` : `delete ${label}`}
              onClick={(e) => {
                e.stopPropagation()
                if (armed) void deleteSession(session.sessionId)
                else setArmed(true)
              }}
            >
              {armed ? 'sure?' : <IconX size={13} />}
            </button>
          </div>
          <span className="row-sub">
            {session.messages} messages · {whenLabel(session.modified * 1000)}
          </span>
        </>
      )}
    </div>
  )
}

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
  const projects = useApp((state) => state.projects)
  const createProject = useApp((state) => state.createProject)
  const renameProject = useApp((state) => state.renameProject)
  const deleteProject = useApp((state) => state.deleteProject)
  const view = useApp((state) => state.view)
  const setView = useApp((state) => state.setView)
  const connection = useApp((state) => state.connection)
  const theme = useApp((state) => state.theme)
  const toggleTheme = useApp((state) => state.toggleTheme)

  const [addingProject, setAddingProject] = useState(false)
  const [projectDraft, setProjectDraft] = useState('')
  const [renamingProject, setRenamingProject] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [armedProject, setArmedProject] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!armedProject) return
    const timer = setTimeout(() => setArmedProject(null), 3000)
    return () => clearTimeout(timer)
  }, [armedProject])

  const storeEnabled = (hello?.storeEnabled ?? true) && (flavor?.storeEnabled ?? true)
  const projectIds = new Set(projects.map((p) => p.id))
  const standalone = sessionRows.filter((s) => !s.projectId || !projectIds.has(s.projectId))
  const byProject = new Map<string, SessionRow[]>()
  for (const s of sessionRows) {
    if (s.projectId && projectIds.has(s.projectId)) {
      byProject.set(s.projectId, [...(byProject.get(s.projectId) || []), s])
    }
  }

  function submitNewProject() {
    const name = projectDraft.trim()
    setAddingProject(false)
    setProjectDraft('')
    if (name) void createProject(name)
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-logo" src={logo} alt="" />
        <span className="brand-name">{flavor?.productName || 'agentd'}</span>
        <span
          className={`dot ${connection === 'open' ? 'dot-ok' : 'dot-off'}`}
          title={connection === 'open' ? 'connected' : 'not connected'}
        />
      </div>

      <button className="new-chat" onClick={() => newSession()}>
        <IconPlus size={16} /> New chat
      </button>

      <div className="section-label">
        <IconSparkle size={13} /> Agents
      </div>
      <div className="agent-list">
        {agents.map((agent) => (
          <button
            key={agent.id}
            className={`row ${agent.id === currentAgentId ? 'row-active' : ''}`}
            onClick={() => void selectAgent(agent.id)}
            title="opens this agent's most recent conversation"
          >
            <span className="row-title">{agent.name || agent.id}</span>
            <span className="row-sub">{agent.id}</span>
          </button>
        ))}
      </div>

      <div className="section-label section-label-row">
        <IconFolder size={13} /> Projects
        <span className="section-spacer" />
        <button
          type="button"
          className="section-action"
          title="new project"
          aria-label="new project"
          onClick={() => {
            setAddingProject(true)
            setProjectDraft('')
          }}
        >
          <IconPlus size={14} />
        </button>
      </div>
      <div className="project-list">
        {addingProject && (
          <input
            className="rename-input"
            placeholder="project name…"
            value={projectDraft}
            autoFocus
            onChange={(e) => setProjectDraft(e.target.value)}
            onBlur={submitNewProject}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                submitNewProject()
              } else if (e.key === 'Escape') {
                e.preventDefault()
                setAddingProject(false)
              }
            }}
          />
        )}
        {projects.map((project) => {
          const chats = byProject.get(project.id) || []
          const isCollapsed = collapsed[project.id] ?? false
          const isRenaming = renamingProject === project.id
          const isArmed = armedProject === project.id
          return (
            <div key={project.id} className="project">
              {isRenaming ? (
                <input
                  className="rename-input"
                  value={renameDraft}
                  autoFocus
                  onChange={(e) => setRenameDraft(e.target.value)}
                  onBlur={() => {
                    if (renameDraft.trim()) void renameProject(project.id, renameDraft.trim())
                    setRenamingProject(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      if (renameDraft.trim()) void renameProject(project.id, renameDraft.trim())
                      setRenamingProject(null)
                    } else if (e.key === 'Escape') {
                      e.preventDefault()
                      setRenamingProject(null)
                    }
                  }}
                />
              ) : (
                <div
                  className="row project-row"
                  role="button"
                  tabIndex={0}
                  aria-expanded={!isCollapsed}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setCollapsed((c) => ({ ...c, [project.id]: !isCollapsed }))
                    }
                  }}
                  onClick={() => setCollapsed((c) => ({ ...c, [project.id]: !isCollapsed }))}
                >
                  <div className="session-row-main">
                    <span className="project-caret">
                      {isCollapsed ? <IconChevronRight size={13} /> : <IconChevronDown size={13} />}
                    </span>
                    <span className="row-title">{project.name}</span>
                    <button
                      type="button"
                      className="hover-btn"
                      title="new chat in this project"
                      aria-label={`new chat in ${project.name}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        newSession(project.id)
                      }}
                    >
                      <IconPlus size={13} />
                    </button>
                    <button
                      type="button"
                      className="hover-btn"
                      title="rename project"
                      aria-label={`rename ${project.name}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        setRenameDraft(project.name)
                        setRenamingProject(project.id)
                      }}
                    >
                      <IconPencil size={13} />
                    </button>
                    <button
                      type="button"
                      className={`hover-btn ${isArmed ? 'hover-btn-danger' : ''}`}
                      title={
                        isArmed
                          ? 'click again to delete the project (its chats stay, as standalone)'
                          : 'delete project'
                      }
                      aria-label={isArmed ? `confirm delete ${project.name}` : `delete ${project.name}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (isArmed) void deleteProject(project.id)
                        else setArmedProject(project.id)
                      }}
                    >
                      {isArmed ? 'sure?' : <IconX size={13} />}
                    </button>
                  </div>
                </div>
              )}
              {!isCollapsed && (
                <div className="project-sessions">
                  {chats.map((session) => (
                    <SessionItem
                      key={session.sessionId}
                      session={session}
                      active={session.sessionId === currentSessionKey}
                      onOpen={() => void resumeSession(session.sessionId)}
                    />
                  ))}
                  {chats.length === 0 && <div className="row-sub pad">no chats yet — use +</div>}
                </div>
              )}
            </div>
          )
        })}
        {projects.length === 0 && !addingProject && (
          <div className="row-sub pad">group chats into projects with +</div>
        )}
      </div>

      <div className="section-label">
        <IconChat size={13} /> Chats
      </div>
      <div className="session-list">
        {standalone.slice(0, 30).map((session) => (
          <SessionItem
            key={session.sessionId}
            session={session}
            active={session.sessionId === currentSessionKey}
            onOpen={() => void resumeSession(session.sessionId)}
          />
        ))}
        {standalone.length === 0 && <div className="row-sub pad">no saved chats yet</div>}
      </div>

      <div className="sidebar-footer">
        {storeEnabled && (
          <button className={`nav ${view === 'store' ? 'nav-active' : ''}`} onClick={() => setView('store')}>
            <IconDownload size={15} /> Store
          </button>
        )}
        <button className={`nav ${view === 'settings' ? 'nav-active' : ''}`} onClick={() => setView('settings')}>
          <IconSliders size={15} /> Settings
        </button>
        <button
          className="nav nav-icon"
          title={theme === 'light' ? 'switch to dark theme' : 'switch to light theme'}
          aria-label={theme === 'light' ? 'switch to dark theme' : 'switch to light theme'}
          onClick={toggleTheme}
        >
          {theme === 'light' ? <IconMoon size={15} /> : <IconSun size={15} />}
        </button>
      </div>
    </aside>
  )
}
