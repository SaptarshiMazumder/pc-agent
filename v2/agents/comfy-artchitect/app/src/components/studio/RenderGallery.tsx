/* Latest renders — real downloaded outputs from `.studio/state.json`. Tiles use the daemon's
 * guarded thumbnails; the original /file is requested only after a tile opens its modal. The
 * first tile is the hero (2×2); one dashed in-flight slot appears while a run is going. */

import { useState } from 'react'

import { thumbnailUrl, type Artifact } from '../../agentd/artifacts'
import { FileModal } from './FileModal'
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
  const [open, setOpen] = useState<Artifact | null>(null)

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
            <button
              type="button"
              key={r.path}
              className={`st-tile${i === 0 ? ' is-hero' : ''}`}
              title={`Open ${r.filename}`}
              aria-haspopup="dialog"
              onClick={() =>
                setOpen({
                  path: r.path,
                  name: r.filename,
                  mime: '',
                  kind: 'image',
                  size: r.bytes,
                })
              }
            >
              <img src={thumbnailUrl(r.path)} alt={r.filename} loading="lazy" decoding="async" />
              <span className="st-tile-cap">{i === 0 ? fmt(r) : r.filename}</span>
            </button>
          ))}
          {running && (
            <div className="st-tile is-pending">
              <span className="st-tile-dot" />
              <span className="st-tile-pending-cap">rendering…</span>
            </div>
          )}
        </div>
      )}
      {open && <FileModal file={open} onClose={() => setOpen(null)} />}
    </section>
  )
}
