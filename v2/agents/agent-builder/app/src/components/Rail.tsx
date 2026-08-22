/* The rail: start a piece of work, pick one you were doing, or open settings.
 *
 * TWO BUTTONS, NAMED AFTER THE WORK, not one named after the medium. It was a single "New chat",
 * which is what Agent Builder does rather than what it is FOR — every conversation then opened on
 * an empty composer and began by guessing whether this was a new agent or an existing one. The
 * guess is now a question, asked once, before the chat (see StartModal).
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
  onCreate,
  onEdit,
  onSettings,
  status,
  daemonVersion,
}: {
  open: boolean
  onToggle: () => void
  chats: ChatRow[]
  openKey: string
  onOpenChat: (key: string) => void
  onCreate: () => void
  onEdit: () => void
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

      <button className="new-btn" onClick={onCreate}>
        <span className="plus">+</span>
        <span>Create new agent</span>
      </button>
      <button className="rail-item edit-btn" onClick={onEdit}>
        <span className="ico">✎</span>
        <span>Edit an agent</span>
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
