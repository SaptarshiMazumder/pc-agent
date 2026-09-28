/* Every model download and node-pack install, one row each, while any is going — above the
 * composer, beside the background-jobs strip.
 *
 * WHAT IT ANSWERS: "is it doing anything, and how long?". A tool's progress used to be one text
 * line in a closed tool row — "GPU download downloading (36%)" among five others — and a stuck
 * install looked exactly like a slow one. Here each file has its own bar, speed and time left,
 * and ends as done or failed.
 *
 * THE NUMBERS ARE THE MACHINE'S: the GPU worker writes them (progress.json), comfy_install reads
 * them once per poll into studio_state, and the window's one studio poll brings them here. A
 * node pack clones with git, which gives no percentage, so it has a state only.
 *
 * SHOWN while anything installs, and for a minute after the last row ended, so "done" and
 * "failed" are seen rather than vanishing the moment they happen.
 */

import type { StudioInstall, StudioState } from './studio/useStudioState'

const ENDED = new Set(['done', 'failed'])
/** How long a finished row stays on screen. */
const LINGER_S = 60
/** A row not updated this long has stopped reporting: it is not shown as still going. */
const STALE_S = 180

const MIB = 1024 * 1024

function size(bytes: number): string {
  return bytes >= 1024 * MIB ? `${(bytes / (1024 * MIB)).toFixed(1)} GB` : `${Math.round(bytes / MIB)} MB`
}

function eta(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
    : s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

function Row({ row, pack }: { row: StudioInstall; pack: boolean }) {
  const ended = ENDED.has(row.state)
  const total = row.total || 0
  const received = row.received || 0
  const pct = row.state === 'done' ? 100 : total ? Math.min(100, Math.floor((received * 100) / total)) : null
  const speed = row.bytes_per_second || 0
  const left = !ended && total && speed > 0 ? (total - received) / speed : null
  const label =
    row.state === 'failed' ? 'failed'
      : row.state === 'done' ? 'done'
        : pack ? 'installing'
          : row.state === 'verifying' ? 'checking file'
            : row.state === 'retrying' ? 'retrying'
              : row.state === 'starting' ? 'starting'
                : 'downloading'
  return (
    <div className={`ip-row is-${row.state === 'failed' ? 'failed' : ended ? 'done' : 'going'}`}>
      <div className="ip-line">
        <span className="ip-kind">{pack ? 'node pack' : 'model'}</span>
        <span className="ip-name" title={row.name}>
          {row.name}
        </span>
        <span className="ip-meta">
          {label}
          {pct !== null && row.state !== 'done' && row.state !== 'failed' && ` · ${pct}%`}
          {!ended && total > 0 && ` · ${size(received)} of ${size(total)}`}
          {!ended && speed > 0 && ` · ${(speed / MIB).toFixed(1)} MiB/s`}
          {left !== null && ` · ${eta(left)} left`}
        </span>
      </div>
      <div
        className={`ip-bar${pct === null && !ended ? ' is-indeterminate' : ''}`}
        role="progressbar"
        aria-label={row.name}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
      >
        <span style={pct === null ? undefined : { width: `${pct}%` }} />
      </div>
      {row.state === 'failed' && row.error && <div className="ip-error">{row.error}</div>}
    </div>
  )
}

export function InstallProgressPanel({ state }: { state: StudioState }) {
  const now = Date.now() / 1000
  const shown = (r: StudioInstall): boolean =>
    ENDED.has(r.state) ? now - r.updated_at < LINGER_S : now - r.updated_at < STALE_S
  const packs = (state.installs?.node_packs || []).filter(shown)
  const models = (state.installs?.models || []).filter(shown)
  if (!packs.length && !models.length) return null
  return (
    <div className="ip" role="status" aria-live="polite">
      {packs.map((r) => (
        <Row key={`p:${r.name}`} row={r} pack />
      ))}
      {models.map((r) => (
        <Row key={`m:${r.name}`} row={r} pack={false} />
      ))}
    </div>
  )
}
