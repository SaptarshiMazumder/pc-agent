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
  ChevronDown,
  ChevronRight,
  CreditCard,
  Loader2,
  RefreshCw,
  MessageSquareText,
  Settings2,
  SquarePen,
} from 'lucide-react'

import { BrandMark } from './BrandMark'
import { useState, type ReactNode } from 'react'


import { ProfileMenu } from '../common/auth/ProfileMenu'
import type { Auth } from '../common/auth/useAuth'
import { useApp, type View } from '../state/store'
import SessionItem from './SessionItem'

/** The destinations that are not the conversation. Each is a shared module — see App.tsx. */
const DESTINATIONS: { id: View; label: string; icon: JSX.Element }[] = [
  { id: 'credits', label: 'Credits', icon: <CreditCard size={15} /> },
  // NO ORGANISATIONS ROW. Comfy Penguin is bought and used by one person -- the ComfyUI
  // instance it drives belongs to that person -- so a teams page is a destination that only
  // ever says "You are not in an organization yet."
  //
  // THE VIEW ITSELF STAYS MOUNTED in App.tsx, and that is not an oversight. validate_agent
  // requires every windowed agent to render `common/orgs` (ui_rules._REQUIRED_COMPONENTS) so
  // that seats and invites are always THE shared page rather than an agent's own guess at
  // one; it detects the module by path, not by a rail entry. Dropping the import to match
  // this row would fail the next validate, and packing would ship an agent a colleague can
  // never be invited into.
  { id: 'settings', label: 'Settings', icon: <Settings2 size={15} /> },
]

export function Sidebar({
  view,
  onView,
  onNewChat,
  account,
  status,
  name = 'This agent',
  extraDestinations = [],
  afterDestinations = [],
  middle,
  counts = {},
  showPrimary = true,
  showConversation = true,
  groupLabel = '',
  sharedGroupLabel = '',
  onRefreshChats,
  onDeleteChat,
  onRenameChat,
  onDuplicateChat,
}: {
  view: View
  onView: (v: View) => void
  onNewChat: () => void
  /** The window's one auth state — owned by App, so the menu and the card cannot disagree. */
  account: Auth
  /** The daemon connection — the run-mode badge reads/sets the mode through it. */
  status: string
  /** What this agent is called. Yours to set. */
  name?: string
  /** A TEMPLATE's own screens, rendered above the shared three. This is how a dashboard variant
   *  gets a nav entry without shipping its own copy of this file — the base is written once. */
  extraDestinations?: { id: View; label: string; icon: JSX.Element }[]
  /** Rows drawn UNDER the shared three (credits, orgs, settings) — the app's own screens that
   *  belong after the account rather than before the conversation. Same shape as `extra`. */
  afterDestinations?: { id: View; label: string; icon: JSX.Element }[]
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
  /** Delete one saved conversation. The ⋯ menu arms before it fires; see ChatMenu. */
  onDeleteChat?: (sessionId: string) => Promise<void>
  /** Retitle one saved conversation. An empty title clears a manual name and lets
   *  auto-titling resume, so it is not treated as a cancel. */
  onRenameChat?: (sessionId: string, title: string) => Promise<void>
  /** Fork one saved conversation — transcript and all. */
  onDuplicateChat?: (sessionId: string) => Promise<void>
  /** EACH IS OPTIONAL AND EACH IS ONE MENU ITEM. A window that cannot do one passes nothing
   *  and that row is absent, rather than present and inert. Pass none and the ⋯ never
   *  appears at all. */
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

  /* WHICH ROW IS BEING DELETED, by session id — the row shows a spinner where its ⋯ was.
     THE CONFIRMATION MOVED INTO THE MENU: a pair of ✓/✗ buttons used to arm on the row itself,
     live on every hover, a pixel from the row you were trying to open, in a list you scan
     constantly. ChatMenu's Delete arms in place instead ("Click again to delete"), which is one
     step further from the cursor and keeps the row free of destructive controls at rest. There
     is still no undo — the daemon drops the transcript — so the arm is still the whole margin. */
  const [deleting, setDeleting] = useState('')
  const remove = (sessionId: string): void => {
    if (!onDeleteChat) return
    setDeleting(sessionId)
    void onDeleteChat(sessionId).finally(() => setDeleting(''))
  }

  /* RECENT COLLAPSES. The list is the one thing in this rail with no upper bound, and a person
     who is working out of the destinations above it should be able to put it away. Open by
     default: it is why most people look here. */
  const [recentOpen, setRecentOpen] = useState(true)

  return (
    <aside className="rail sidebar">
      <div className="brand">
        {/* The agent's mark: the penguin architect, ink on the lime tile. Inline so it takes the
            tile's colour rather than shipping a second drawing per theme (BrandMark.tsx). */}
        <span className="brand-tile" aria-hidden="true">
          <BrandMark size={28} />
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

      <nav className="nav-items">
        {/* STARTING A CONVERSATION IS A ROW, not a filled slab. It was the latter — the argument
            being that it is the one consequential action and everything else is a place to go.
            The trouble is that it is ALSO the most repeated one, and a button styled to be
            unmissable is still unmissable on the four-hundredth look while having crowded out
            the rows beneath it the whole time. Every assistant worth copying — and the builder
            window next door — makes it the first row and lets position carry the emphasis. */}
        {showPrimary && (
          <button className="nav-item" onClick={onNewChat}>
            <span className="nav-ico">
              <SquarePen size={15} strokeWidth={1.7} />
            </span>
            <span className="nav-item-label">New conversation</span>
          </button>
        )}

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
        {afterDestinations.map((d) => (
          <button
            key={d.id}
            className={`nav-item${view === d.id ? ' on' : ''}`}
            onClick={() => onView(d.id)}
          >
            <span className="nav-ico">{d.icon}</span>
            <span className="nav-item-label">{d.label}</span>
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
              /* THE WHOLE HEAD IS THE TOGGLE, and the caret and the refresh only appear under the
                 cursor — at rest this is a label, which is all it needs to be. */
              <div
                className="section-label section-head"
                onClick={() => setRecentOpen((v) => !v)}
                title={`${recentOpen ? 'collapse' : 'expand'} recent conversations`}
              >
                <span className="section-title">Recent</span>
                {onRefreshChats && (
                  <button
                    type="button"
                    className="section-add"
                    onClick={(e) => {
                      // The head toggles; this must not, or reloading would also fold the list
                      // away underneath the spinner.
                      e.stopPropagation()
                      refresh()
                    }}
                    disabled={refreshing}
                    title="Reload the conversation list"
                    aria-label="Reload the conversation list"
                  >
                    <RefreshCw
                      size={13}
                      strokeWidth={2}
                      className={refreshing ? 'ld-spin' : undefined}
                    />
                  </button>
                )}
                <span className="section-caret">
                  {recentOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </span>
              </div>
            )}
            {!recentOpen ? null : chatsLoading ? (
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
                    <SessionItem
                      key={c.sessionId}
                      session={c}
                      active={view === 'chat' && c.sessionId === currentKey}
                      busy={deleting === c.sessionId}
                      onOpen={() => open(c.sessionId)}
                      onRename={
                        onRenameChat
                          ? (title) => void onRenameChat(c.sessionId, title)
                          : undefined
                      }
                      onDuplicate={
                        onDuplicateChat ? () => void onDuplicateChat(c.sessionId) : undefined
                      }
                      onDelete={onDeleteChat ? () => remove(c.sessionId) : undefined}
                    />
                  ))}
                  {chats.length === 0 && (
                    <div className="row-sub list-empty">no conversations yet</div>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </div>

      <div className="rail-spacer" />

      <div className="rail-foot">
        {/* NO RUN-MODE BADGE. This agent has no local mode: the platform's keys pay for every model
            call, on the web and on a desktop alike, so a Cloud/Local switch was a control that
            could only ever say "Cloud". */}
        {/* WHO IS SIGNED IN, and the way to Credits from beside the identity it bills. Shared —
            do not replace it with one of your own; see src/common/README.md. */}
        <ProfileMenu {...account} onCredits={() => onView('credits')} />
        {/* THE ADDRESS, in words: the avatar is one letter, and one letter does not answer "which
            account is this" for someone with two. Where the Cloud/Local badge used to sit. */}
        <span className="rail-foot-email" title={account.auth?.email || ''}>
          {account.auth?.email || (account.auth?.signedIn ? 'Signed in' : 'Not signed in')}
        </span>
      </div>
    </aside>
  )
}
