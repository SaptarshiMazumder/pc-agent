/* Chat history as a standing column, not a dropdown you have to remember exists.
 *
 * A dropdown answers "take me back to that one". A visible list answers "which one was it" —
 * and for an agent whose conversations each produced a different workflow, that second question
 * is the one people actually have.
 */

import { useState } from 'react'
import type { SessionRow } from '../agentd'

export function HistoryPanel({
  sessions,
  current,
  onOpen,
  onRename,
  onDelete,
  onFork,
  onNew,
  floating,
  onClose,
}: {
  sessions: SessionRow[]
  current: string
  onOpen: (key: string) => void
  onRename: (key: string, title: string) => Promise<void>
  onDelete: (key: string) => Promise<void>
  onFork: (key: string) => Promise<void>
  onNew: () => void
  /** Narrow window: the same panel, over the conversation instead of beside it. */
  floating?: boolean
  /** Only when floating — a panel covering the conversation needs its own way out. */
  onClose?: () => void
}) {
  const [editing, setEditing] = useState('')
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')

  /** One place every action reports its failure. The gateway refuses some of these — a session
   *  with a live run cannot be deleted — and a refusal that is swallowed looks like a button
   *  that does nothing, so the user presses it again. */
  const run = async (action: () => Promise<void>) => {
    setError('')
    try {
      await action()
    } catch (e) {
      setError(String(e))
    }
  }

  const commit = async (key: string) => {
    setEditing('')
    await run(() => onRename(key, draft.trim()))
  }

  return (
    <aside className={`side-panel ${floating ? 'floating' : ''}`}>
      <header className="panel-head">
        <h2>Chat history</h2>
        <span className="grow" />
        <button className="ghost" onClick={onNew}>
          New
        </button>
        {onClose && (
          <button className="icon" onClick={onClose} title="Close">
            ✕
          </button>
        )}
      </header>

      {error && <p className="panel-error">{error}</p>}

      {!sessions.length && <p className="panel-empty">No conversations yet.</p>}

      <ul className="cards">
        {sessions.map((s) => (
          <li key={s.sessionId} className={`card ${s.sessionId === current ? 'on' : ''}`}>
            {editing === s.sessionId ? (
              <input
                autoFocus
                className="card-rename"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => void commit(s.sessionId)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void commit(s.sessionId)
                  if (e.key === 'Escape') setEditing('')
                }}
              />
            ) : (
              <button className="card-open" onClick={() => onOpen(s.sessionId)}>
                <span className="card-title">{s.title || 'Untitled conversation'}</span>
                {s.preview && <span className="card-preview">{s.preview}</span>}
              </button>
            )}
            <div className="card-foot">
              <span className="card-when">{s.updatedAt ? when(s.updatedAt) : ''}</span>
              <button
                className="icon"
                title="Rename"
                onClick={() => {
                  setEditing(s.sessionId)
                  setDraft(s.title || '')
                  setError('')
                }}
              >
                ✎
              </button>
              {/* Fork before delete, and both report their refusal — the gateway answers some
                  of these with {ok:false} on a successful frame, which a caller that only
                  catches rejections renders as "nothing happened". */}
              <button
                className="icon"
                title="Fork — copy this conversation and its context into a new one"
                onClick={() => void run(() => onFork(s.sessionId))}
              >
                ⑂
              </button>
              <button
                className="icon"
                title="Delete"
                onClick={() => void run(() => onDelete(s.sessionId))}
              >
                🗑
              </button>
            </div>
          </li>
        ))}
      </ul>
    </aside>
  )
}

/** Relative for anything recent, a date beyond that. "3h ago" answers "is this the one I was
 *  just in?" — an ISO timestamp makes you work it out yourself. */
function when(iso: string): string {
  const then = new Date(iso).getTime()
  if (!Number.isFinite(then)) return ''
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`
  return new Date(then).toLocaleDateString()
}
