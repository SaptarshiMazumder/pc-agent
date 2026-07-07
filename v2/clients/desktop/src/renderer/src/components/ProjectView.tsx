import { useMemo } from 'react'
import { ArrowLeft, Folder, SquarePen } from 'lucide-react'

import { hashColor } from '../lib/agentPresentation'
import { useApp } from '../state/store'
import SessionItem from './SessionItem'

/** One project's detail page (view:'project', uses currentProjectId). Shows the project's chats
 *  ACROSS every agent (from the cross-agent Recents, filtered by projectId) + a "New chat in
 *  [project]" action. Lead-agent + members UI arrives in Layer B. */
export default function ProjectView() {
  const projects = useApp((s) => s.projects)
  const currentProjectId = useApp((s) => s.currentProjectId)
  const recents = useApp((s) => s.recents)
  const agents = useApp((s) => s.agents)
  const newSession = useApp((s) => s.newSession)
  const resumeSession = useApp((s) => s.resumeSession)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const setProjectLead = useApp((s) => s.setProjectLead)

  const project = projects.find((p) => p.id === currentProjectId)
  const chats = useMemo(
    () => recents.filter((r) => r.projectId === currentProjectId),
    [recents, currentProjectId]
  )

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

  return (
    <div className="settings">
      <div className="settings-inner settings-wide">
        <div className="settings-head">
          <div className="settings-head-titles">
            <button className="btn ghost btn-back" onClick={() => setView('projects')} title="Back to Projects">
              <ArrowLeft size={14} />Projects
            </button>
            <div className="page-title project-title">
              <span style={{ display: 'inline-flex', color: hashColor(project.id) }}><Folder size={20} /></span>
              {project.name}
            </div>
            <div className="page-sub">{chats.length} chat{chats.length === 1 ? '' : 's'} in this project.</div>
          </div>
          <div className="settings-head-actions">
            <button
              className="btn primary"
              title={`this project answers as ${(agents.find((a) => a.id === (project.defaultAgentId || 'main'))?.name) || 'main'}`}
              onClick={() => newSession(project.id)}
            >
              <SquarePen size={14} />New chat in project
            </button>
          </div>
        </div>

        {/* One quiet control, no jargon: which agent picks up when you chat in this project.
            Default main — it pulls in specialists automatically, so most users never touch this. */}
        <div className="proj-answers">
          <span className="proj-answers-label">Answers as</span>
          <select
            className="settings-select"
            value={project.defaultAgentId || 'main'}
            onChange={(e) => void setProjectLead(project.id, e.target.value === 'main' ? '' : e.target.value)}
            title="which agent answers new chats in this project (it can still bring in other agents)"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id === 'main' ? `${a.name || 'main'} (default)` : (a.name || a.id)}
              </option>
            ))}
          </select>
        </div>

        <div className="settings-group">
          <div className="settings-section">Chats</div>
          {chats.length === 0 && (
            <div className="settings-card">
              <div className="settings-empty">No chats yet — start one with “New chat in project”.</div>
            </div>
          )}
          <div className="project-chats">
            {chats.map((s) => (
              <SessionItem
                key={s.sessionId}
                session={s}
                active={view === 'chat' && s.sessionId === currentSessionKey}
                onOpen={() => void resumeSession(s.sessionId)}
                withAgentDot
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
