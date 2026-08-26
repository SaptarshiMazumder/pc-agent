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
 */

import { Building2, CreditCard, MessageSquarePlus, Settings2 } from 'lucide-react'
import type { ReactNode } from 'react'

import { when } from '../agentd/sessions'
import { ProfileMenu } from '../common/auth/ProfileMenu'
import type { Auth } from '../common/auth/useAuth'
import { useApp, type View } from '../state/store'

/** The destinations that are not the conversation. Each is a shared module — see App.tsx. */
const DESTINATIONS: { id: View; label: string; icon: JSX.Element }[] = [
  { id: 'credits', label: 'Credits & billing', icon: <CreditCard size={16} /> },
  { id: 'orgs', label: 'Organizations', icon: <Building2 size={16} /> },
  { id: 'settings', label: 'Settings', icon: <Settings2 size={16} /> },
]

export function Sidebar({
  view,
  onView,
  onNewChat,
  account,
  status,
  name = 'This agent',
  extraDestinations = [],
  middle,
}: {
  view: View
  onView: (v: View) => void
  onNewChat: () => void
  /** The window's one auth state — owned by App, so the menu and the card cannot disagree. */
  account: Auth
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
}) {
  const chats = useApp((s) => s.chats)
  const openSession = useApp((s) => s.openSession)
  const currentKey = useApp((s) => s.currentSessionKey)

  return (
    <aside className="rail sidebar">
      <div className="brand">
        <span className="brand-name">{name}</span>
        {/* The daemon connection. A window that merely stops responding is unexplainable, and
            this is the explanation — so it lives where it is always visible rather than turning
            up only once something has already gone wrong. */}
        <span className="live" title={`daemon: ${status}`}>
          <span className="live-dot" style={{ opacity: status === 'open' ? 1 : 0.3 }} />
        </span>
      </div>

      <div className="nav-rows">
        <button className="nav-row" onClick={onNewChat}>
          <MessageSquarePlus size={16} />
          <span className="nav-row-label">New chat</span>
        </button>
      </div>

      <div className="sidebar-scroll">
        {middle !== undefined ? (
          middle
        ) : (
          <>
        {chats.length > 0 && <div className="section-title">Recent</div>}
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

      <div className="nav-rows footer-nav">
        {[...extraDestinations, ...DESTINATIONS].map((d) => (
          <button
            key={d.id}
            className={`nav-row ${view === d.id ? 'on' : ''}`}
            onClick={() => onView(d.id)}
          >
            {d.icon}
            <span className="nav-row-label">{d.label}</span>
          </button>
        ))}

        {/* WHO IS SIGNED IN, and the way to Credits from beside the identity it bills. Shared —
            do not replace it with one of your own; see src/common/README.md. */}
        <ProfileMenu {...account} onCredits={() => onView('credits')} />
      </div>
    </aside>
  )
}
