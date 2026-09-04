/* Latest renders — real downloaded outputs from `.studio/state.json`, served through the
 * daemon's guarded /file endpoint. The first tile is the hero (2×2); one dashed in-flight slot
 * appears while a run is going. Empty is a sentence, not a blank grid. */

import { fileUrl } from '../../agentd/artifacts'
import type { StudioRender } from './useStudioState'

const fmt = (r: StudioRender) =>
  [r.filename, r.w && r.h ? `${r.w}×${r.h}` : ''].filter(Boolean).join(' · ')

export function RenderGallery({
  renders,
  running,
  query,
}: {
  renders: StudioRender[]
  running: boolean
  query: string
}) {
  const q = query.trim().toLowerCase()
  const shown = (q ? renders.filter((r) => r.filename.toLowerCase().includes(q)) : renders).slice(0, 6)

  return (
    <section className="st-panel">
      <div className="st-panel-head">
        <h2>Latest renders</h2>
        {renders.length > 6 && <span className="st-panel-note">{renders.length} total</span>}
      </div>

      {shown.length === 0 && !running ? (
        <p className="st-empty">
          Nothing downloaded yet — after a run, the agent pulls the outputs here with
          <code> comfy_download</code>.
        </p>
      ) : (
        <div className="st-tiles">
          {shown.map((r, i) => (
            <a
              key={r.path}
              className={`st-tile${i === 0 ? ' is-hero' : ''}`}
              href={fileUrl(r.path)}
              target="_blank"
              rel="noreferrer"
              title={r.filename}
            >
              <img src={fileUrl(r.path)} alt={r.filename} loading="lazy" />
              <span className="st-tile-cap">{i === 0 ? fmt(r) : r.filename}</span>
            </a>
          ))}
          {running && (
            <div className="st-tile is-pending">
              <span className="st-tile-dot" />
              <span className="st-tile-pending-cap">rendering…</span>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
