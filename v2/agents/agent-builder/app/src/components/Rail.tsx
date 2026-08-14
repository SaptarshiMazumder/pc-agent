/* The rail: one button for starting a conversation, one place to switch views, and the list of
 * conversations.
 *
 * "New chat", not "New agent" — the screen it opens offers BOTH paths: work on an existing agent,
 * or describe a new one. There used to be a second copy of this button in the topbar under the
 * other name, and two names for one action is what made the chrome unreadable.
 *
 * Settings lives here too, and only here. The topbar carried a duplicate gear justified by "the
 * rail folds on a small window" — but it folds to an icon strip, so this stays clickable and the
 * duplicate was guarding nothing.
 */

import type { View } from '../App'
import { when, type ChatRow } from '../agentd/sessions'

export function Rail({
  open,
  onToggle,
  view,
  onView,
  chats,
  openKey,
  onOpenChat,
  onNewChat,
  status,
  daemonVersion,
}: {
  open: boolean
  onToggle: () => void
  view: View
  onView: (v: View) => void
  chats: ChatRow[]
  openKey: string
  onOpenChat: (key: string) => void
  onNewChat: () => void
  status: string
  daemonVersion: string
}) {
  return (
    <aside className="rail glass" id="rail">
      <div className="rail-head">
        <div className="brand">
          <span className="mark">◈</span>
          <span className="brand-name">Agent Builder</span>
        </div>
        <button className="icon-btn" onClick={onToggle} title="Collapse sidebar">
          {open ? '‹' : '›'}
        </button>
      </div>

      <button className="new-btn" onClick={onNewChat}>
        <span className="plus">+</span> New chat
      </button>

      <nav className="rail-nav">
        <button
          className={`nav-item ${view === 'build' ? 'active' : ''}`}
          onClick={() => onView('build')}
        >
          <span className="ico">✦</span>Build
        </button>
        <button
          className={`nav-item ${view === 'settings' ? 'active' : ''}`}
          onClick={() => onView('settings')}
        >
          <span className="ico">⚙</span>Settings
        </button>
      </nav>

      <div className="rail-section">
        <span className="rail-label">Chats</span>
        <span className="count">{chats.length || ''}</span>
      </div>

      <ul className="agent-list">
        {chats.length === 0 ? (
          <li className="rail-empty">No conversations yet.</li>
        ) : (
          chats.map((c) => (
            <li
              key={c.sessionId}
              className={`chat-row ${c.sessionId === openKey ? 'on' : ''}`}
              title={c.snippet || c.title || c.sessionId}
              onClick={() => onOpenChat(c.sessionId)}
            >
              <div className="agent-meta">
                <div className="chat-top">
                  <span className="agent-name">{c.title || 'Untitled'}</span>
                  <span className="chat-when">{when(c.modified)}</span>
                </div>
                <div className="agent-sub">{c.snippet || `${c.messages || 0} messages`}</div>
              </div>
            </li>
          ))
        )}
      </ul>

      {/* Connection state, always visible: when the daemon goes away, a window that merely stops
          responding is unexplainable, and this is the explanation. */}
      <div className="rail-foot">
        <span className={`status ${status === 'open' ? 'live' : status === 'closed' ? 'down' : ''}`}>
          {status === 'open' ? 'connected' : status === 'closed' ? 'disconnected' : status}
        </span>
        <span className="ver">{daemonVersion}</span>
      </div>
    </aside>
  )
}
