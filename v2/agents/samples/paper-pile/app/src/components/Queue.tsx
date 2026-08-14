import type { QueueItem } from '../useQueue'

/** Per-item state, always. The word AND the colour carry it — colour alone is unreadable to
 *  anyone who does not distinguish these hues, and this is the one control that has to be
 *  scannable at a glance across forty rows. */
export function Queue({
  items,
  onRun,
  onClear,
  disabled,
}: {
  items: QueueItem[]
  onRun: () => void
  onClear: () => void
  disabled: boolean
}) {
  const pending = items.filter((i) => i.state === 'queued').length
  const failed = items.filter((i) => i.state === 'failed').length
  const done = items.filter((i) => i.state === 'done').length
  const working = items.some((i) => i.state === 'working' || i.state === 'uploading')

  return (
    <section className="queue-wrap">
      <div className="queue-bar">
        <span className="queue-count">
          {items.length ? `${done} done · ${failed} failed · ${pending} to go` : ''}
        </span>
        <span className="queue-actions">
          {(done || failed) > 0 && (
            <button className="ghost" onClick={onClear}>
              Clear
            </button>
          )}
          <button className="prime" onClick={onRun} disabled={disabled || working || !pending}>
            {working ? 'reading…' : `Ingest ${pending || ''}`}
          </button>
        </span>
      </div>

      <ul className="queue">
        {items.length === 0 && <li className="empty">Nothing queued yet.</li>}
        {items.map((i) => (
          <li key={i.id} className={`q ${i.state}`}>
            <div className="q-head">
              <span className="q-name" title={i.name}>
                {i.name}
              </span>
              <span className="q-state">{i.state}</span>
            </div>
            {i.note && <span className="q-note">{i.note}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
