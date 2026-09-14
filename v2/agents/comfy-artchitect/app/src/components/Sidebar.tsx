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
  Check,
  CreditCard,
  Loader2,
  RefreshCw,
  Trash2,
  X,
  MessageSquareText,
  Plus,
  Settings2,
  Sparkles,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

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
  showPrimary = true,
  showConversation = true,
  groupLabel = '',
  sharedGroupLabel = '',
  onRefreshChats,
  onDeleteChat,
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
  /** The filled New-conversation button. A template whose conversations live somewhere else — the
   *  dashboard puts them in its top bar and its agent panel — turns it off rather than showing a
   *  second button that means the same thing. */
  showPrimary?: boolean
  /** The Conversation destination. Off for a template that has no full-width chat view: a nav row
   *  that selects a screen this window does not have is worse than no row. */
  showConversation?: boolean
  /** A heading over `extraDestinations` — "Sections", say. Only drawn when there are entries to
   *  head, so a template with none gets no orphan label. */
  groupLabel?: string
  /** A heading over the shared three (credits / organizations / settings). Two labelled groups is
   *  what turns a flat list of seven rows into "where I work" and "my account". */
  sharedGroupLabel?: string
  /** Re-read the saved-conversation list. Returns a promise so the button can spin for exactly
   *  as long as the read takes — App owns the fetch, this owns the asking. */
  onRefreshChats?: () => Promise<void>
  /** Delete one saved conversation. Confirmed in the row first; see the note below. */
  onDeleteChat?: (sessionId: string) => Promise<void>
}) {
  const chats = useApp((s) => s.chats)
  const chatsLoading = useApp((s) => s.chatsLoading)
  const chatsError = useApp((s) => s.chatsError)
  const openSession = useApp((s) => s.openSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  const connected = status === 'open'

  /** Open a saved chat. THE TRANSCRIPT IS NOT FETCHED HERE, and that is the change: this used to
   *  call `loadHistory` as well as App's own effect, so one click asked the daemon for the same
   *  history twice — and neither request recorded that it was in flight, which is why the chat
   *  column showed its "new conversation" screen the whole time. App owns the fetch now, in one
   *  guarded effect keyed to the open session, and the rail is presentation again. */
  const open = (sessionId: string): void => openSession(sessionId)

  /* THE REFRESH BUTTON'S OWN BUSY STATE, not the store's `chatsLoading`. That flag swaps the
     whole list for a spinner, which is right when there is nothing to show and wrong here: a
     manual refresh should leave the rows you are reading exactly where they are and show its
     progress on the control you pressed. */
  const [refreshing, setRefreshing] = useState(false)
  const refresh = (): void => {
    if (refreshing || !onRefreshChats) return
    setRefreshing(true)
    void onRefreshChats().finally(() => setRefreshing(false))
  }

  /* WHICH ROW IS ASKING "ARE YOU SURE", by session id. A two-step confirm in the row rather than
     a window.confirm(): the native dialog steals focus, cannot be styled, and reads as a browser
     interruption rather than as part of this list. Deleting a conversation cannot be undone —
     the daemon removes the transcript — so one deliberate second click is the whole margin.
     Nothing is armed at rest, and opening another row's confirm disarms the previous one. */
  const [confirming, setConfirming] = useState('')
  const [deleting, setDeleting] = useState('')
  const remove = (sessionId: string): void => {
    if (!onDeleteChat) return
    setConfirming('')
    setDeleting(sessionId)
    void onDeleteChat(sessionId).finally(() => setDeleting(''))
  }

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
      {showPrimary && (
        <button className="nav-primary" onClick={onNewChat}>
          <Plus size={16} strokeWidth={2.2} />
          <span>New conversation</span>
        </button>
      )}

      <nav className="nav-items">
        {showConversation && (
          <button
            className={`nav-item${view === 'chat' ? ' on' : ''}`}
            onClick={() => onView('chat')}
          >
            <span className="nav-ico">
              <MessageSquareText size={15} strokeWidth={1.7} />
            </span>
            <span className="nav-item-label">Conversation</span>
          </button>
        )}

        {extraDestinations.length > 0 && groupLabel && (
          <div className="nav-group">{groupLabel}</div>
        )}
        {extraDestinations.map((d) => (
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

        {sharedGroupLabel && <div className="nav-group">{sharedGroupLabel}</div>}
        {DESTINATIONS.map((d) => (
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
            {/* The heading stands while the list is being read, so the spinner has something to
                belong to. Keyed to the same condition as the body below — a label over nothing is
                worse than no label. */}
            {(chatsLoading || chats.length > 0) && (
              <div className="section-label">
                <span>Recent</span>
                {onRefreshChats && (
                  <button
                    type="button"
                    className="label-btn"
                    onClick={refresh}
                    disabled={refreshing}
                    title="Reload the conversation list"
                    aria-label="Reload the conversation list"
                  >
                    <RefreshCw
                      size={12}
                      strokeWidth={2}
                      className={refreshing ? 'ld-spin' : undefined}
                    />
                  </button>
                )}
              </div>
            )}
            {chatsLoading ? (
              /* NOT AN EMPTY LIST. Those look identical and mean opposite things, and the moment
                 it matters most is right after an account switch: the rows are gone because they
                 were the last user's, and the new ones are still on their way. */
              <div className="rail-loading">
                <Loader2 className="ld-spin" size={14} strokeWidth={1.9} />
                <span>Loading conversations…</span>
              </div>
            ) : (
              <>
                {/* ABOVE the list, not instead of it. A failed READ leaves no rows and this is
                    all there is to see; a refused DELETE ("session has an active run") happens
                    with a full list on screen, and hiding it to show one line would be a second
                    thing going wrong. */}
                {chatsError && <p className="rail-error">{chatsError}</p>}
                <div className="agents-list">
                  {chats.map((c) => (
                    <div
                      key={c.sessionId}
                      className={`row-line${
                        confirming === c.sessionId || deleting === c.sessionId ? ' is-armed' : ''
                      }`}
                    >
                      <button
                        className={`row ${view === 'chat' && c.sessionId === currentKey ? 'on' : ''}`}
                        onClick={() => open(c.sessionId)}
                        title={c.title || 'Untitled'}
                      >
                        <span className="row-main">
                          <span className="row-title">
                            {c.running && <span className="row-live" title="running" />}
                            {c.title || 'Untitled'}
                          </span>
                          <span className="row-sub">{c.snippet || when(c.modified)}</span>
                        </span>
                      </button>
                      {/* OVERLAID ON THE ROW, NOT BESIDE IT. As a flex sibling this stole ~24px
                          from every title and, worse, sat OUTSIDE the row's own background — so
                          the hover highlight stopped short of it and the button hung in the gap
                          past the end of the bubble. Absolute, over the right edge, behind a
                          scrim that masks the text underneath: the title keeps the full width and
                          the actions reserve no space at all until they are wanted. Same idiom as
                          `.session-row .row-actions` further up this stylesheet. */}
                      {onDeleteChat && (
                        <div className="row-acts">
                          {deleting === c.sessionId ? (
                            <span className="row-act is-busy" title="Deleting…">
                              <Loader2 size={13} strokeWidth={2} className="ld-spin" />
                            </span>
                          ) : confirming === c.sessionId ? (
                            <>
                              <button
                                type="button"
                                className="row-act is-danger"
                                onClick={() => remove(c.sessionId)}
                                title="Delete this conversation — this cannot be undone"
                                aria-label="Confirm delete"
                              >
                                <Check size={13} strokeWidth={2.4} />
                              </button>
                              <button
                                type="button"
                                className="row-act"
                                onClick={() => setConfirming('')}
                                title="Keep it"
                                aria-label="Cancel delete"
                              >
                                <X size={13} strokeWidth={2.4} />
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              className="row-act"
                              onClick={() => setConfirming(c.sessionId)}
                              title={
                                c.running
                                  ? 'Running — stop it before deleting'
                                  : 'Delete this conversation'
                              }
                              aria-label="Delete this conversation"
                            >
                              <Trash2 size={13} strokeWidth={1.9} />
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
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
