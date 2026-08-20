/* The sidebar — the same shape as agentd's, because it is the same product.
 *
 * WHY IT MIRRORS agentd. A user moves between the desktop app and this window constantly while
 * building, and two sidebars that arrange the same facts differently make that a re-orientation
 * every time. So: brand and connection at the top, the things you START at the top, the agents
 * you HAVE in the middle, the conversations you had below them, and the account controls pinned
 * to the bottom. Same order, same rows, same avatars.
 *
 * WHAT IS DELIBERATELY ABSENT. agentd has a Projects row; this window cannot have one, because
 * `projects.list` is host-only and the daemon refuses it on an app connection. An omitted row is
 * honest; a row that answers "method not allowed" is not.
 *
 * TWO ENTRY POINTS, NAMED AFTER THE WORK, not one named after the medium. It was a single
 * "New chat" — which is what this agent does rather than what it is FOR — so every conversation
 * opened on an empty composer and began by guessing whether this was a new agent or an existing
 * one. The guess is now a question, asked once, before the chat (see StartModal).
 */

import {
  LayoutGrid,
  PanelLeft,
  Pencil,
  Search,
  Settings as SettingsIcon,
  SquarePen,
  Sun,
} from 'lucide-react'
import { useMemo, useState } from 'react'

import { agentColor, agentInitials } from '../lib/agentPresentation'
import type { AgentRow } from '../agentd/roster'
import { when, type ChatRow } from '../agentd/sessions'
import { ProfileMenu } from './ProfileMenu'
import type { AuthState } from '@agentd/client'

export function Rail({
  open,
  onToggle,
  chats,
  openKey,
  onOpenChat,
  agents,
  selectedId,
  onPickAgent,
  onCreate,
  onEdit,
  onSettings,
  onCredits,
  onMyAgents,
  view,
  auth,
  authError,
  onSignIn,
  onSignOut,
  status,
  daemonVersion,
}: {
  open: boolean
  onToggle: () => void
  chats: ChatRow[]
  openKey: string
  onOpenChat: (key: string) => void
  /** Openable agents only — this window is not one of its own subjects. */
  agents: AgentRow[]
  selectedId: string
  onPickAgent: (id: string) => void
  onCreate: () => void
  onEdit: () => void
  onSettings: () => void
  onCredits: () => void
  onMyAgents: () => void
  view: string
  auth: AuthState | null
  authError: string
  onSignIn: () => Promise<void> | void
  onSignOut: () => Promise<void> | void
  status: string
  daemonVersion: string
}) {
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)

  // Filters BOTH lists from one box, exactly as agentd's does. A search that only reached one of
  // them would be a search you have to know the shape of before you use it.
  const q = query.trim().toLowerCase()
  const shownAgents = useMemo(
    () =>
      !q
        ? agents
        : agents.filter((a) =>
            `${a.name || ''} ${a.id} ${a.tagline || ''}`.toLowerCase().includes(q),
          ),
    [agents, q],
  )
  const shownChats = useMemo(
    () =>
      !q
        ? chats
        : chats.filter((c) => `${c.title || ''} ${c.snippet || ''}`.toLowerCase().includes(q)),
    [chats, q],
  )

  return (
    /* `rail` places it in the shell grid; `sidebar` is the agentd-shaped content inside. */
    <aside className="rail sidebar">
      <div className="brand">
        <span className="brand-mark">◈</span>
        <span className="brand-name">Agent Builder</span>
        {/* Connection state, in the one place a user already looks for it. When the daemon goes
            away a window that merely stops responding is unexplainable; this is the explanation. */}
        <span className="live" title={`${status}${daemonVersion ? ` · agentd ${daemonVersion}` : ''}`}>
          <span className="live-dot" style={{ opacity: status === 'open' ? 1 : 0.3 }} />
          {status === 'open' ? 'live' : status === 'closed' ? 'down' : '…'}
        </span>
        <button
          className="icon-btn"
          onClick={onToggle}
          title={open ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          <PanelLeft size={17} />
        </button>
      </div>

      <div className="nav-rows">
        <button className="nav-row" onClick={onCreate} title="Create a new agent">
          <SquarePen size={17} />
          <span className="nav-row-label">New agent</span>
        </button>
        <button className="nav-row" onClick={onEdit} title="Work on an agent you already have">
          <Pencil size={17} />
          <span className="nav-row-label">Edit an agent</span>
        </button>
        {searching ? (
          <input
            className="nav-search"
            autoFocus
            placeholder="Search agents & chats…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onBlur={() => !query && setSearching(false)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setQuery('')
                setSearching(false)
              }
            }}
          />
        ) : (
          <button className="nav-row" onClick={() => setSearching(true)} title="Search">
            <Search size={17} />
            <span className="nav-row-label">Search</span>
          </button>
        )}
      </div>

      <div className="sidebar-scroll">
        <div className="section-title">Agents</div>
        <div className="agents-list">
          {shownAgents.length === 0 ? (
            <div className="row-sub list-empty">
              {q ? 'nothing matches' : 'no agents yet — start with New agent'}
            </div>
          ) : (
            shownAgents.map((a) => (
              <button
                key={a.id}
                className={`agent-row ${a.id === selectedId ? 'active' : ''}`}
                onClick={() => onPickAgent(a.id)}
                title={a.description || a.tagline || a.id}
              >
                <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>
                  {agentInitials(a.name, a.id)}
                </span>
                <span className="row-main">
                  <span className="row-title">{a.name || a.id}</span>
                  <span className="row-sub">{a.tagline || a.id}</span>
                </span>
              </button>
            ))
          )}
        </div>

        <div className="section-title">
          Recents
          <span className="count">{chats.length || ''}</span>
        </div>
        <div className="agents-list">
          {shownChats.length === 0 ? (
            <div className="row-sub list-empty">{q ? 'nothing matches' : 'no conversations yet'}</div>
          ) : (
            shownChats.map((c) => (
              <button
                key={c.sessionId}
                className={`chat-row ${c.sessionId === openKey ? 'active' : ''}`}
                title={c.snippet || c.title || c.sessionId}
                onClick={() => onOpenChat(c.sessionId)}
              >
                {/* The dot is hashed from the SESSION, so a conversation keeps its colour across
                    restarts — the same trick agentd uses to make a long list scannable. */}
                <span className="chat-dot" style={{ background: agentColor(undefined, c.sessionId) }} />
                <span className="row-main">
                  <span className="row-title">{c.title || 'Untitled'}</span>
                  <span className="row-sub">{c.snippet || when(c.modified)}</span>
                </span>
              </button>
            ))
          )}
        </div>
      </div>

      <div className="footer-nav">
        <ProfileMenu
          auth={auth}
          error={authError}
          onCredits={onCredits}
          onSignIn={onSignIn}
          onSignOut={onSignOut}
        />
        <button
          className={`icon-btn footer-icon push-end ${view === 'myagents' ? 'active' : ''}`}
          title="My Agents"
          onClick={onMyAgents}
        >
          <LayoutGrid size={17} />
        </button>
        <button className="icon-btn footer-icon" title="Settings" onClick={onSettings}>
          <SettingsIcon size={17} />
        </button>
        {/* PLACEHOLDER, and deliberately inert. This window is dark-only: there are no light
            tokens to switch to yet. The button holds the position agentd's theme toggle occupies
            so the two footers do not drift apart before the feature lands. */}
        <button className="icon-btn footer-icon" title="Light mode — not available yet" disabled>
          <Sun size={17} />
        </button>
      </div>
    </aside>
  )
}
