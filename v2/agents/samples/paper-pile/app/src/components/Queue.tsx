import type { QueueItem } from '../useQueue'

/** Per-item state, always. The word AND the colour carry it — colour alone is unreadable to
 *  anyone who does not distinguish these hues, and this is the one control that has to be
 *  scannable at a glance across forty rows. */
export function Queue({
  items,
  onRun,
  onStop,
  onClear,
  disabled,
}: {
  items: QueueItem[]
  onRun: () => void
  onStop: () => void
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
          {/* While it runs, the only useful control is the one that gets you out. A batch with no
              way to stop it is a batch you cannot correct after realising the first file is wrong. */}
          {working ? (
            <button className="ghost" onClick={onStop}>
              Stop
            </button>
          ) : (
            <button className="prime" onClick={onRun} disabled={disabled || !pending}>
              Ingest {pending || ''}
            </button>
          )}
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
            {i.state === 'working' && !i.note && (
              <span className="q-note">reading it — this can take a few minutes</span>
            )}
            {i.note && <span className="q-note">{i.note}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
