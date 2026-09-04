/* Every run the bridge recorded, newest first — name, checkpoint, steps, duration, status,
 * when. The grid-as-table from the design; horizontal scroll inside the panel under ~760px. */

import type { StudioRun } from './useStudioState'

function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`
  return `${Math.round(s / 86400)} d ago`
}

export function RunHistory({
  runs,
  rangeDays,
  query,
}: {
  runs: StudioRun[]
  rangeDays: number
  query: string
}) {
  const q = query.trim().toLowerCase()
  const shown = runs
    .filter((r) => Date.now() / 1000 - r.ts <= rangeDays * 86400)
    .filter((r) => !q || r.name.toLowerCase().includes(q) || r.checkpoint.toLowerCase().includes(q))

  return (
    <section className="st-panel st-history">
      <div className="st-panel-head">
        <h2>Run history</h2>
        {runs.length > shown.length && (
          <span className="st-panel-note">{runs.length} recorded</span>
        )}
      </div>
      {shown.length === 0 ? (
        <p className="st-empty">
          {q ? 'No runs match the search.' : 'No runs in this range yet.'}
        </p>
      ) : (
        <div className="st-table-scroll">
          <div className="st-table-head st-table-grid">
            <span>Workflow</span>
            <span>Checkpoint</span>
            <span>Steps</span>
            <span>Time</span>
            <span>Status</span>
            <span className="is-right">When</span>
          </div>
          {shown.slice(0, 20).map((r, i) => (
            <div key={`${r.ts}-${i}`} className="st-table-row st-table-grid">
              <span className="st-cell-name">{r.name}</span>
              <span className="st-cell-mono">{r.checkpoint.replace(/\.(safetensors|ckpt|gguf|sft)$/i, '') || '—'}</span>
              <span className="st-cell-mono">{r.steps ?? '—'}</span>
              <span className="st-cell-mono">{r.status === 'complete' ? `${r.duration.toFixed(1)}s` : '—'}</span>
              <span>
                <span className={`st-status-chip is-${r.status}`}>{r.status}</span>
              </span>
              <span className="st-cell-when is-right">{ago(r.ts)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
