import { useEffect, useMemo, useState } from 'react'
import { SquarePen, FolderOpen, Sparkles, File, Folder, Image, Film, Music, FileText } from 'lucide-react'

import { gateway } from '../gateway/client'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { whenLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import SessionItem from './SessionItem'

interface WorkspaceFile {
  name: string
  kind: string
  size: number
  modified: number
}
interface SkillRow {
  name: string
  description: string
}
interface AgentDetail {
  id: string
  name: string
  description?: string
  tagline?: string
  version?: string
  model?: string
  color?: string
  workspace?: string
  workspaceFiles: WorkspaceFile[]
  skills: SkillRow[]
}

function fmtBytes(n: number): string {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function KindIcon({ kind }: { kind: string }) {
  const s = 16
  if (kind === 'folder') return <Folder size={s} />
  if (kind === 'image') return <Image size={s} />
  if (kind === 'video') return <Film size={s} />
  if (kind === 'audio') return <Music size={s} />
  if (kind === 'file') return <File size={s} />
  return <FileText size={s} />
}

/** One agent's detail page (view:'agent', uses viewedAgentId). The agent analogue of the Project
 *  page: its chats (from cross-agent Recents), its workspace files, and its skills. "New chat with
 *  [agent]" starts a fresh conversation as that agent. */
export default function AgentView() {
  const agents = useApp((s) => s.agents)
  const viewedAgentId = useApp((s) => s.viewedAgentId)
  const recents = useApp((s) => s.recents)
  const newChatWithAgent = useApp((s) => s.newChatWithAgent)
  const resumeSession = useApp((s) => s.resumeSession)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const view = useApp((s) => s.view)
  const connection = useApp((s) => s.connection)

  const agent = agents.find((a) => a.id === viewedAgentId)
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [loading, setLoading] = useState(false)

  const chats = useMemo(
    () => recents.filter((r) => r.agentId === viewedAgentId),
    [recents, viewedAgentId]
  )

  useEffect(() => {
    if (!viewedAgentId || connection !== 'open') return
    let cancelled = false
    setDetail(null)
    setLoading(true)
    gateway
      .request<AgentDetail>('agents.detail', { agentId: viewedAgentId })
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch(() => { if (!cancelled) setDetail(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [viewedAgentId, connection])

  if (!agent) {
    return (
      <div className="settings">
        <div className="settings-inner settings-wide">
          <div className="settings-empty">This agent no longer exists.</div>
        </div>
      </div>
    )
  }

  const files = detail?.workspaceFiles || []
  const skills = detail?.skills || []

  return (
    <div className="settings">
      <div className="settings-inner settings-wide">
        <div className="settings-head">
          <div className="settings-head-titles">
            <div className="agent-hero">
              <span className="avatar avatar-lg" style={{ background: agentColor(agent.color, agent.id) }}>
                {agentInitials(agent.name, agent.id)}
              </span>
              <div>
                <div className="page-title">{agent.name || agent.id}</div>
                <div className="page-sub">
                  {agent.tagline || 'agent'}
                  {detail?.version ? ` · v${detail.version}` : ''}
                  {detail?.model ? ` · ${detail.model}` : ''}
                </div>
              </div>
            </div>
          </div>
          <div className="settings-head-actions">
            <button className="btn primary" onClick={() => newChatWithAgent(agent.id)}>
              <SquarePen size={14} />New chat with {agent.name || agent.id}
            </button>
          </div>
        </div>

        {/* Chats */}
        <div className="settings-group">
          <div className="settings-section">Chats</div>
          {chats.length === 0 && (
            <div className="settings-card"><div className="settings-empty">No chats with this agent yet.</div></div>
          )}
          <div className="project-chats">
            {chats.map((s) => (
              <SessionItem
                key={s.sessionId}
                session={s}
                active={view === 'chat' && s.sessionId === currentSessionKey}
                onOpen={() => void resumeSession(s.sessionId)}
              />
            ))}
          </div>
        </div>

        {/* Workspace */}
        <div className="settings-group">
          <div className="settings-section"><FolderOpen size={13} style={{ verticalAlign: '-2px', marginRight: 5 }} />Workspace</div>
          {loading && !detail && <div className="settings-empty">Loading…</div>}
          {detail && files.length === 0 && (
            <div className="settings-card"><div className="settings-empty">No files in this agent's workspace yet.</div></div>
          )}
          {files.map((f) => (
            <div className="ds-row file-row" key={f.name}>
              <div className="ds-icon"><KindIcon kind={f.kind} /></div>
              <div className="ds-main">
                <div className="ds-name">{f.name}</div>
                <div className="ds-sub">
                  {f.kind}{f.size ? ` · ${fmtBytes(f.size)}` : ''}
                  {f.modified ? ` · ${whenLabel(f.modified * 1000)}` : ''}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Skills */}
        <div className="settings-group">
          <div className="settings-section"><Sparkles size={13} style={{ verticalAlign: '-2px', marginRight: 5 }} />Skills</div>
          {detail && skills.length === 0 && (
            <div className="settings-card"><div className="settings-empty">No skills available to this agent.</div></div>
          )}
          {skills.map((sk) => (
            <div className="ds-row skill-row" key={sk.name}>
              <div className="ds-icon"><Sparkles size={16} /></div>
              <div className="ds-main">
                <div className="ds-name">{sk.name}</div>
                <div className="ds-sub">{sk.description}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
