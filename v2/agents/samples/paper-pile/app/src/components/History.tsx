import { useState } from 'react'
import type { SessionRow } from '../agentd'

/** Past conversations, as a side rail.
 *
 *  The transcript already lives in the daemon — every chat this agent has had is on disk. An app
 *  that does not read it makes each session look like the agent's first, and the user has no way
 *  back to the answer they got last week. */
export function History({
  rows,
  error,
  activeId,
  onOpen,
  onRename,
  onDelete,
  onNew,
}: {
  rows: SessionRow[]
  error: string
  activeId: string
  onOpen: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onNew: () => void
}) {
  const [editing, setEditing] = useState('')
  const [title, setTitle] = useState('')

  const commit = (id: string) => {
    const next = title.trim()
    setEditing('')
    if (next) onRename(id, next)
  }

  return (
    <aside className="history">
      <div className="history-head">
        <h2>Chat history</h2>
        <button className="ghost small" onClick={onNew}>
          New
        </button>
      </div>

      {error && <p className="err">{error}</p>}
      {!error && rows.length === 0 && <p className="muted pad">No saved conversations yet.</p>}

      <ul className="threads">
        {rows.map((r) => (
          <li key={r.sessionId} className={r.sessionId === activeId ? 'on' : ''}>
            {editing === r.sessionId ? (
              <input
                autoFocus
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onBlur={() => commit(r.sessionId)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commit(r.sessionId)
                  if (e.key === 'Escape') setEditing('')
                }}
              />
            ) : (
              <button className="thread" onClick={() => onOpen(r.sessionId)}>
                <span className="thread-title">{r.title || 'Untitled'}</span>
                {r.snippet && <span className="thread-snip">{r.snippet}</span>}
                <span className="thread-meta">
                  {r.messages} message{r.messages === 1 ? '' : 's'}
                  {r.modified ? ` · ${new Date(r.modified * 1000).toLocaleDateString()}` : ''}
                </span>
              </button>
            )}
            <span className="thread-actions">
              <button
                title="Rename"
                onClick={() => {
                  setEditing(r.sessionId)
                  setTitle(r.title)
                }}
              >
                ✎
              </button>
              <button title="Delete" onClick={() => onDelete(r.sessionId)}>
                🗑
              </button>
            </span>
          </li>
        ))}
      </ul>
    </aside>
  )
}
