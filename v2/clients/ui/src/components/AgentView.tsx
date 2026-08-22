import { useEffect, useMemo, useState } from 'react'
import { SquarePen, FolderOpen, Sparkles, MessageSquare, ChevronRight, ChevronDown, AppWindow, Cpu } from 'lucide-react'

import { gateway } from '../gateway/client'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { appLaunchUrl } from '../lib/artifacts'
import { platform } from '../lib/platform'
import { useApp } from '../state/store'
import SearchBox from './SearchBox'
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
  /** the agent's own app UI (daemon-served /apps/<id>/), when it ships one */
  app?: { title: string; url: string; mode?: 'window' | 'browser' } | null
}

type Tab = 'chats' | 'workspace' | 'skills' | 'settings'

/** One selectable model, as `config.get` publishes it. */
interface ModelOption { value: string; label?: string; group?: string }

/**
 * PER-AGENT SETTINGS — this agent's own model, layered over the daemon's.
 *
 * On a hosted daemon the whole document lands in the signed-in account's own config, so this is
 * "MY figure-creator runs on Opus" and not "everyone's does". The daemon resolves it per run
 * (config.agents[<id>] over the daemon-wide values), so a change applies to the next message with
 * no restart — and to nobody else's messages, ever.
 */
function AgentSettings({ agentId }: { agentId: string }) {
  const [models, setModels] = useState<ModelOption[]>([])
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [scoped, setScoped] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const res = (await gateway.request('config.get')) as {
          values?: Record<string, any>
          catalogs?: Record<string, ModelOption[]>
          accountScoped?: boolean
        }
        if (!alive) return
        setModels(res.catalogs?.models || [])
        setValues((res.values?.agents || {})[agentId] || {})
        setScoped(!!res.accountScoped)
      } catch (e) {
        if (alive) setNote(String((e as Error)?.message || e))
      }
    })()
    return () => { alive = false }
  }, [agentId])

  const save = async (patch: Record<string, unknown>): Promise<void> => {
    const next = { ...values, ...patch }
    setValues(next)
    setNote('Saving…')
    try {
      const res = (await gateway.request('config.set', {
        patch: { agents: { [agentId]: next } }
      })) as { saved?: boolean; error?: string }
      setNote(res?.saved === false ? res.error || 'Not saved' : 'Saved')
    } catch (e) {
      setNote(String((e as Error)?.message || e))
    }
  }

  return (
    <div className="settings-group">
      <div className="settings-section"><Cpu size={13} />Model for this agent</div>
      <div className="settings-card">
        <div className="settings-row">
          <div className="settings-label">
            <div className="k">Model</div>
            <div className="d">
              overrides the default for this agent only. “Default” follows whatever the general
              model is set to.
            </div>
          </div>
          <div className="settings-ctl">
            <select
              className="settings-input"
              value={String(values.model || '')}
              onChange={(e) => void save({ model: e.target.value })}
            >
              <option value="">Default</option>
              {models.map((m) => (
                <option key={m.value} value={m.value}>{m.label || m.value}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="settings-row">
          <div className="settings-label">
            <div className="k">Reasoning effort</div>
            <div className="d">how much this agent thinks before answering</div>
          </div>
          <div className="settings-ctl">
            <select
              className="settings-input"
              value={String(values.reasoning_effort || '')}
              onChange={(e) => void save({ reasoning_effort: e.target.value })}
            >
              <option value="">Default</option>
              {['off', 'low', 'medium', 'high'].map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
        </div>
      </div>
      <p className="settings-help">
        {scoped
          ? 'Yours alone — stored with your account, applied to your chats with this agent. Other people using it keep their own.'
          : 'Applies to this agent on this machine.'}
        {note ? ` · ${note}` : ''}
      </p>
    </div>
  )
}

/** One agent's detail page (view:'agent', uses viewedAgentId). Fixed header + tabs; only the
 *  body scrolls. TABS: Chats (searchable table w/ preview) / Workspace (file tree) / Skills
 *  (each opens its SKILL.md in the Canvas, view + edit). */
export default function AgentView() {
  const agents = useApp((s) => s.agents)
  const viewedAgentId = useApp((s) => s.viewedAgentId)
  const recents = useApp((s) => s.recents)
  const newChatWithAgent = useApp((s) => s.newChatWithAgent)
  const openAgentApp = useApp((s) => s.openAgentApp)
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
  // the agent's own app UI — the hello roster carries it, agents.detail confirms it
  const appInfo = detail?.app ?? agent.app ?? null
  const openApp = async (): Promise<void> => {
    if (!appInfo) return
    // Honor the AUTHOR's declared presentation. `mode: "window"` still means a dedicated
    // desktop window — an app that asked for the whole screen gets it.
    //
    // The DEFAULT changed (apps-plan P4): "browser" used to mean the system browser, which
    // threw the user out of the app they had just installed the agent into. It now means
    // EMBEDDED — the app opens as a page inside agentd (view:'app'), which is what installing
    // something into a product is supposed to feel like. The window route stays one click away
    // from there, so nothing became unreachable.
    if (appInfo.mode === 'window') {
      const res = await platform.openAppWindow?.(appLaunchUrl(appInfo, agent.id), appInfo.title)
      if (res?.ok) return
      window.open(appLaunchUrl(appInfo, agent.id)) // no bridge (browser) — fall back
      return
    }
    openAgentApp(agent.id)
  }

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
              {appInfo && (
                <button
                  className="btn"
                  title={`open ${appInfo.title} ${appInfo.mode === 'window' ? 'in its own window' : 'in the browser'}`}
                  onClick={() => void openApp()}
                >
                  <AppWindow size={14} />Open app
                </button>
              )}
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
              <button className={tab === 'settings' ? 'on' : ''} onClick={() => setTab('settings')}>
                <Cpu size={14} />Settings
              </button>
              <button className={tab === 'skills' ? 'on' : ''} onClick={() => setTab('skills')}>
                <Sparkles size={13} />Skills
              </button>
            </div>
            {tab === 'chats' && chats.length > 0 && (
              <SearchBox className="entity-search" value={query} onChange={setQuery} placeholder="Search chats" />
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

          {tab === 'settings' && <AgentSettings agentId={agent.id} />}

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
