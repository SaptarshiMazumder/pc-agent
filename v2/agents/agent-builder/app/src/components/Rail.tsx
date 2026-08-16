/* The rail: start a conversation, pick one, or open settings.
 *
 * "New chat", not "New agent" — the screen it opens offers BOTH paths: work on an existing agent,
 * or describe a new one. There used to be a second copy of this button in the topbar under the
 * other name, and two names for one action is what made the chrome unreadable.
 *
 * Settings is a ROW HERE and a MODAL when clicked, not a second view. It was half of a two-way
 * switch, which meant opening settings closed the conversation you opened them because of.
 */

import { when, type ChatRow } from '../agentd/sessions'

export function Rail({
  open,
  onToggle,
  chats,
  openKey,
  onOpenChat,
  onNewChat,
  onSettings,
  status,
  daemonVersion,
}: {
  open: boolean
  onToggle: () => void
  chats: ChatRow[]
  openKey: string
  onOpenChat: (key: string) => void
  onNewChat: () => void
  onSettings: () => void
  status: string
  daemonVersion: string
}) {
  return (
    <aside className="rail">
      <div className="rail-head">
        <div className="brand">
          <span className="mark">◈</span>
          <span className="brand-name">Agent Builder</span>
        </div>
        <button
          className="icon-btn"
          onClick={onToggle}
          title={open ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          {open ? '⟨' : '⟩'}
        </button>
      </div>

      <button className="new-btn" onClick={onNewChat}>
        <span className="plus">+</span>
        <span>New chat</span>
      </button>

      <div className="rail-label">
        <span>Chats</span>
        <span className="count">{chats.length || ''}</span>
      </div>

      <ul className="chat-list">
        {chats.length === 0 ? (
          <li className="rail-empty">No conversations yet.</li>
        ) : (
          chats.map((c) => (
            <li key={c.sessionId}>
              <button
                className={`chat-row ${c.sessionId === openKey ? 'on' : ''}`}
                title={c.snippet || c.title || c.sessionId}
                onClick={() => onOpenChat(c.sessionId)}
              >
                <span className="chat-top">
                  <span className="chat-name">{c.title || 'Untitled'}</span>
                  <span className="chat-when">{when(c.modified)}</span>
                </span>
                <span className="chat-sub">{c.snippet || `${c.messages || 0} messages`}</span>
              </button>
            </li>
          ))
        )}
      </ul>

      <div className="rail-label">
        <span>Tools</span>
      </div>
      <button className="rail-item" onClick={onSettings}>
        <span className="ico">⚙</span>
        <span>Settings</span>
      </button>

      {/* Connection state, always visible: when the daemon goes away, a window that merely stops
          responding is unexplainable, and this is the explanation. */}
      <div className="conn-card">
        <span className={`dot ${status === 'open' ? 'live' : status === 'closed' ? 'down' : ''}`} />
        <span className="conn-text">
          {status === 'open' ? 'connected' : status === 'closed' ? 'disconnected' : status}
        </span>
        <span className="ver">{daemonVersion}</span>
      </div>
    </aside>
  )
}
