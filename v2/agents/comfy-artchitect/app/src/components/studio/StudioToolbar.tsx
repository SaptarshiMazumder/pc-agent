/* The dashboard's sticky toolbar: search, connection pill, the primary action, the account
 * initial. Search is REAL — it filters the history/models/renders panels below (the query
 * lives in StudioDashboard); the pill states the connection, not an invented queue. */

import { Plus, Search } from 'lucide-react'

export function StudioToolbar({
  query,
  onQuery,
  connected,
  onNewRun,
  initial,
}: {
  query: string
  onQuery: (q: string) => void
  connected: boolean
  /** Seeds the composer with a run request — starting a run is the CONVERSATION's job. */
  onNewRun: () => void
  /** One letter for the avatar; '' renders the agent's own mark. */
  initial: string
}) {
  return (
    <div className="st-toolbar">
      <label className="st-search">
        <Search size={13} strokeWidth={2} />
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="search runs, nodes, checkpoints…"
          spellCheck={false}
        />
      </label>
      <span className={`st-status ${connected ? 'is-ok' : ''}`}>
        <span className="st-status-dot" />
        {connected ? 'connected' : 'offline'}
      </span>
      <button className="st-primary" onClick={onNewRun}>
        <Plus size={15} strokeWidth={2.2} />
        New run
      </button>
      <span className="st-avatar">{initial || 'CA'}</span>
    </div>
  )
}
