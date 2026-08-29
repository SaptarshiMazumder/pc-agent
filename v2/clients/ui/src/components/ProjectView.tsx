import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Folder, SquarePen, FolderOpen, MessageSquare } from 'lucide-react'

import { agentLabel, hashColor, MAIN_AGENT_ID } from '../lib/agentPresentation'
import { listableAgents } from '../lib/standaloneApps'
import { useApp } from '../state/store'
import SearchBox from './SearchBox'
import SessionItem from './SessionItem'
import WorkspaceTree from './WorkspaceTree'

/** One project's detail page (view:'project', uses currentProjectId). Fixed header + tabs; only
 *  the body scrolls. TABS: Chats (searchable, cross-agent, w/ preview) / Workspace (the project's
 *  SHARED file tree every agent in the project reads and writes — plan §11). */
export default function ProjectView() {
  const projects = useApp((s) => s.projects)
  const currentProjectId = useApp((s) => s.currentProjectId)
  const recents = useApp((s) => s.recents)
  const agents = useApp((s) => s.agents)
  const hello = useApp((s) => s.hello)
  const newSession = useApp((s) => s.newSession)
  const resumeSession = useApp((s) => s.resumeSession)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const setProjectLead = useApp((s) => s.setProjectLead)
  const [tab, setTab] = useState<'chats' | 'workspace'>('chats')
  const [query, setQuery] = useState('')

  const project = projects.find((p) => p.id === currentProjectId)
  const chats = useMemo(
    () => recents.filter((r) => r.projectId === currentProjectId),
    [recents, currentProjectId]
  )
  const q = query.trim().toLowerCase()
  const shownChats = q
    ? chats.filter((c) => `${c.title} ${c.snippet || ''}`.toLowerCase().includes(q))
    : chats

  useEffect(() => {
    setTab('chats')
    setQuery('')
  }, [currentProjectId])

  if (!project) {
    return (
      <div className="settings">
        <div className="settings-inner settings-wide">
          <div className="settings-empty">This project no longer exists.</div>
          <button className="btn ghost btn-back" onClick={() => setView('projects')}><ArrowLeft size={14} />Projects</button>
        </div>
      </div>
    )
  }

  const leadId = project.defaultAgentId || MAIN_AGENT_ID
  const leadName = agentLabel(agents.find((a) => a.id === leadId)?.name, leadId, hello?.agentName)

  return (
    <div className="entity-page">
      <div className="entity-head">
        <div className="settings-inner settings-wide">
          <button className="btn ghost btn-back" onClick={() => setView('projects')} title="Back to Projects">
            <ArrowLeft size={14} />Projects
          </button>
          <div className="settings-head">
            <div className="settings-head-titles">
              <div className="page-title project-title">
                <span className="ico-tint" style={{ color: hashColor(project.id) }}><Folder size={20} /></span>
                {project.name}
              </div>
              <div className="page-sub">
                {chats.length} chat{chats.length === 1 ? '' : 's'} · answers as{' '}
                <select
                  className="settings-select proj-answers-inline"
                  value={project.defaultAgentId || MAIN_AGENT_ID}
                  onChange={(e) => void setProjectLead(project.id, e.target.value === MAIN_AGENT_ID ? '' : e.target.value)}
                  title="which agent answers new chats in this project (it can still bring in other agents)"
                >
                  {/* a product surface cannot be a project's lead — see standaloneApps */}
                  {listableAgents(agents).map((a) => (
                    <option key={a.id} value={a.id}>
                      {agentLabel(a.name, a.id, hello?.agentName)}{a.id === MAIN_AGENT_ID ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="settings-head-actions">
              <button className="btn primary" title={`this project answers as ${leadName}`} onClick={() => newSession(project.id)}>
                <SquarePen size={14} />New chat in project
              </button>
            </div>
          </div>

          <div className="entity-tabbar">
            <div className="seg entity-tabs">
              <button className={tab === 'chats' ? 'on' : ''} onClick={() => setTab('chats')}>
                <MessageSquare size={13} />Chats
              </button>
              <button className={tab === 'workspace' ? 'on' : ''} onClick={() => setTab('workspace')}>
                <FolderOpen size={13} />Workspace
              </button>
            </div>
            {tab === 'chats' && chats.length > 0 && (
              <SearchBox className="entity-search" value={query} onChange={setQuery} placeholder="Search chats" />
            )}
          </div>
        </div>
      </div>

      <div className="entity-body">
        <div className="settings-inner settings-wide">
          {tab === 'chats' && (
            shownChats.length === 0 ? (
              <div className="settings-empty">{q ? 'No chats match.' : 'No chats yet — start one with “New chat in project”.'}</div>
            ) : (
              <div className="chat-table">
                <div className="chat-table-head"><span>Name</span><span>Modified</span></div>
                {shownChats.map((s) => (
                  <SessionItem
                    key={s.sessionId}
                    session={s}
                    table
                    active={view === 'chat' && s.sessionId === currentSessionKey}
                    onOpen={() => void resumeSession(s.sessionId)}
                    withAgentDot
                  />
                ))}
              </div>
            )
          )}

          {tab === 'workspace' && <WorkspaceTree projectId={project.id} />}
        </div>
      </div>
    </div>
  )
}
