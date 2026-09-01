/* The rail: what this window can show, and who is looking at it.
 *
 * THE SHAPE AND THE CLASS NAMES ARE THE ASSISTANT'S, deliberately. Somebody who uses the assistant
 * and then opens your agent should not have to learn a second place to find their credits or their
 * settings — so the brand sits at the top, the conversations in the middle, and the account at the
 * bottom. Change the middle freely; moving the account and the destinations only costs your users
 * the thing they already knew.
 *
 * ORGANIZATIONS AND CREDITS ARE NOT OPTIONAL FURNITURE. An agent installed by a company is used by
 * people who were invited to it, and one that never shows a seat or a balance simply stops working
 * for them with nothing on screen to explain why.
 *
 * THE ORDER IS DESTINATIONS FIRST, HISTORY SECOND. Where you can go is a short fixed list and it
 * is what a new user is looking for; the conversation list grows without limit and scrolls under
 * it. The old rail buried the destinations at the bottom under that list, so "where are my
 * credits" was a scroll away in a window that had been used for a week.
 */

import {
  Building2,
  CreditCard,
  MessageSquareText,
  Plus,
  Settings2,
  Sparkles,
} from 'lucide-react'
import type { ReactNode } from 'react'

import type { AgentdClient } from '@agentd/client'

import { when } from '../agentd/sessions'
import { ProfileMenu } from '../common/auth/ProfileMenu'
import RunModeBadge from '../common/runmode/RunModeBadge'
import type { Auth } from '../common/auth/useAuth'
import { useApp, type View } from '../state/store'

/** The destinations that are not the conversation. Each is a shared module — see App.tsx. */
const DESTINATIONS: { id: View; label: string; icon: JSX.Element }[] = [
  { id: 'credits', label: 'Credits', icon: <CreditCard size={15} /> },
  { id: 'orgs', label: 'Organizations', icon: <Building2 size={15} /> },
  { id: 'settings', label: 'Settings', icon: <Settings2 size={15} /> },
]

export function Sidebar({
  view,
  onView,
  onNewChat,
  account,
  client,
  status,
  name = 'This agent',
  extraDestinations = [],
  middle,
  counts = {},
}: {
  view: View
  onView: (v: View) => void
  onNewChat: () => void
  /** The window's one auth state — owned by App, so the menu and the card cannot disagree. */
  account: Auth
  /** The daemon connection — the run-mode badge reads/sets the mode through it. */
  client?: AgentdClient
  status: string
  /** What this agent is called. Yours to set. */
  name?: string
  /** A TEMPLATE's own screens, rendered above the shared three. This is how a dashboard variant
   *  gets a nav entry without shipping its own copy of this file — the base is written once. */
  extraDestinations?: { id: View; label: string; icon: JSX.Element }[]
  /** Replaces the MIDDLE of the rail (the Recent-chats list). A workbench-shaped template puts
   *  its sections here and keeps its chat in a side panel instead — same file, same bottom, so
   *  the account and the shared destinations stay single-sourced. */
  middle?: ReactNode
  /** A number to show beside a destination — the balance next to Credits, say. OPTIONAL and
   *  per-id, so a template that has no figure for one simply passes nothing and the row renders
   *  without it. Never invent one: a count that is a guess is worse than no count. */
  counts?: Partial<Record<string, string>>
}) {
  const chats = useApp((s) => s.chats)
  const openSession = useApp((s) => s.openSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  const connected = status === 'open'

  return (
    <aside className="rail sidebar">
      <div className="brand">
        {/* The agent's mark. A gradient tile rather than a logo file, so an agent that never
            ships artwork still has an identity on screen. */}
        <span className="brand-tile" aria-hidden="true">
          <Sparkles size={17} strokeWidth={1.9} />
        </span>
        <span className="brand-text">
          <span className="brand-name">{name}</span>
          {/* The daemon connection. A window that merely stops responding is unexplainable, and
              this is the explanation — so it lives where it is always visible rather than turning
              up only once something has already gone wrong. */}
          <span className="brand-status" title={`daemon: ${status}`}>
            {/* The STATE is a class; the look of each state is the stylesheet's. An inline style
                here would be a visual decision no theme could reach. */}
            <span className={`live-dot${connected ? ' is-live' : ''}`} />
            {connected ? 'connected' : status}
          </span>
        </span>
      </div>

      {/* THE ONE CONSEQUENTIAL ACTION, filled and unmissable. Everything else in this rail is a
          place to go; this is the thing you came to do. */}
      <button className="nav-primary" onClick={onNewChat}>
        <Plus size={16} strokeWidth={2.2} />
        <span>New conversation</span>
      </button>

      <nav className="nav-items">
        <button
          className={`nav-item${view === 'chat' ? ' on' : ''}`}
          onClick={() => onView('chat')}
        >
          <span className="nav-ico">
            <MessageSquareText size={15} strokeWidth={1.7} />
          </span>
          <span className="nav-item-label">Conversation</span>
        </button>

        {[...extraDestinations, ...DESTINATIONS].map((d) => (
          <button
            key={d.id}
            className={`nav-item${view === d.id ? ' on' : ''}`}
            onClick={() => onView(d.id)}
          >
            <span className="nav-ico">{d.icon}</span>
            <span className="nav-item-label">{d.label}</span>
            {counts[d.id] ? <span className="nav-count">{counts[d.id]}</span> : null}
          </button>
        ))}
      </nav>

      <div className="sidebar-scroll">
        {middle !== undefined ? (
          middle
        ) : (
          <>
            {chats.length > 0 && <div className="section-label">Recent</div>}
            <div className="agents-list">
              {chats.map((c) => (
                <button
                  key={c.sessionId}
                  className={`row ${view === 'chat' && c.sessionId === currentKey ? 'on' : ''}`}
                  onClick={() => openSession(c.sessionId)}
                  title={c.title || 'Untitled'}
                >
                  <span className="row-main">
                    <span className="row-title">{c.title || 'Untitled'}</span>
                    <span className="row-sub">{c.snippet || when(c.modified)}</span>
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="rail-spacer" />

      <div className="rail-foot">
        {/* Whose keys pay for model calls — always on screen, click to switch. Shared component;
            fixed "Cloud" on the web (no BYOK there). */}
        <RunModeBadge client={client} />
        {/* WHO IS SIGNED IN, and the way to Credits from beside the identity it bills. Shared —
            do not replace it with one of your own; see src/common/README.md. */}
        <ProfileMenu {...account} onCredits={() => onView('credits')} />
      </div>
    </aside>
  )
}
