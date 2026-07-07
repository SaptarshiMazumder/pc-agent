import { useEffect, useMemo, useState } from 'react'
import { SquarePen, FolderOpen, Sparkles, MessageSquare, Search, ChevronRight, ChevronDown } from 'lucide-react'

import { gateway } from '../gateway/client'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { useApp } from '../state/store'
import SessionItem from './SessionItem'
import WorkspaceTree from './WorkspaceTree'

interface SkillRow {
  name: string
  description: string
  path?: string
  source?: 'own' | 'shared'
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
  skills: SkillRow[]
}

type Tab = 'chats' | 'workspace' | 'skills'

/** One agent's detail page (view:'agent', uses viewedAgentId). Fixed header + tabs; only the
 *  body scrolls. TABS: Chats (searchable table w/ preview) / Workspace (file tree) / Skills
 *  (each opens its SKILL.md in the Canvas, view + edit). */
export default function AgentView() {
  const agents = useApp((s) => s.agents)
  const viewedAgentId = useApp((s) => s.viewedAgentId)
  const recents = useApp((s) => s.recents)
  const newChatWithAgent = useApp((s) => s.newChatWithAgent)
  const resumeSession = useApp((s) => s.resumeSession)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const view = useApp((s) => s.view)
  const connection = useApp((s) => s.connection)
  const openCanvas = useApp((s) => s.openCanvas)

  const agent = agents.find((a) => a.id === viewedAgentId)
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [tab, setTab] = useState<Tab>('chats')
  const [query, setQuery] = useState('')
  const [showShared, setShowShared] = useState(false) // reveal inherited (default-library) skills

  const chats = useMemo(
    () => recents.filter((r) => r.agentId === viewedAgentId),
    [recents, viewedAgentId]
  )
  const q = query.trim().toLowerCase()
  const shownChats = q
    ? chats.filter((c) => `${c.title} ${c.snippet || ''}`.toLowerCase().includes(q))
    : chats

  useEffect(() => {
    setTab('chats')
    setQuery('')
    setShowShared(false)
  }, [viewedAgentId])

  useEffect(() => {
    if (!viewedAgentId || connection !== 'open') return
    let cancelled = false
    setDetail(null)
    gateway
      .request<AgentDetail>('agents.detail', { agentId: viewedAgentId })
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch(() => { if (!cancelled) setDetail(null) })
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

  const skills = detail?.skills || []

  return (
    <div className="entity-page">
      {/* fixed: hero + tabs + (chats) search — does not scroll */}
      <div className="entity-head">
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

          <div className="entity-tabbar">
            <div className="seg entity-tabs">
              <button className={tab === 'chats' ? 'on' : ''} onClick={() => setTab('chats')}>
                <MessageSquare size={13} />Chats
              </button>
              <button className={tab === 'workspace' ? 'on' : ''} onClick={() => setTab('workspace')}>
                <FolderOpen size={13} />Workspace
              </button>
              <button className={tab === 'skills' ? 'on' : ''} onClick={() => setTab('skills')}>
                <Sparkles size={13} />Skills
              </button>
            </div>
            {tab === 'chats' && chats.length > 0 && (
              <div className="search-box entity-search">
                <Search size={15} />
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" spellCheck={false} />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* only this scrolls */}
      <div className="entity-body">
        <div className="settings-inner settings-wide">
          {tab === 'chats' && (
            shownChats.length === 0 ? (
              <div className="settings-empty">{q ? 'No chats match.' : 'No chats with this agent yet.'}</div>
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
                  />
                ))}
              </div>
            )
          )}

          {tab === 'workspace' && <WorkspaceTree agentId={agent.id} />}

          {tab === 'skills' && (() => {
            // own skills show by default; inherited (default-library) ones hide behind a toggle
            // so it's clear which were made for THIS agent
            const own = skills.filter((s) => s.source !== 'shared')
            const shared = skills.filter((s) => s.source === 'shared')
            const skillRow = (sk: SkillRow): JSX.Element => (
              <button
                type="button"
                className="entity-row"
                key={sk.name}
                title="open SKILL.md in the canvas (view & edit)"
                onClick={() => sk.path && openCanvas({ path: sk.path, name: `${sk.name} · SKILL.md`, mime: 'text/markdown', kind: 'file' })}
              >
                <span className="entity-ico"><Sparkles size={15} /></span>
                <span className="entity-main">
                  <span className="entity-name">{sk.name}</span>
                  <span className="entity-sub">{sk.description}</span>
                </span>
              </button>
            )
            return (
              <>
                {own.length > 0 ? (
                  <div className="entity-list">{own.map(skillRow)}</div>
                ) : (
                  <div className="settings-empty">No skills specific to this agent.</div>
                )}
                {shared.length > 0 && (
                  <>
                    <button className="btn ghost show-inherited" onClick={() => setShowShared((v) => !v)}>
                      {showShared ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      {showShared ? 'Hide' : 'Show'} {shared.length} inherited default skill{shared.length === 1 ? '' : 's'}
                    </button>
                    {showShared && <div className="entity-list">{shared.map(skillRow)}</div>}
                  </>
                )}
              </>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
