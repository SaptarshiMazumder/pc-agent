/* The left half of a stage: everything it has made. Click a result to PICK it — what the stages
 * after it are given; the eye opens it in the viewer. Picking is instant and free. */

import { Clock, Eye } from 'lucide-react'

import type { Artifact } from '../../agentd/artifacts'
import type { PlanStage } from '../../agentd/stage-plan'
import { OutputThumb } from './OutputsGrid'

const words = (name: string): string => name.replace(/_/g, ' ')

export function StageResults({
  index,
  stage,
  made,
  picked,
  hint,
  rendering,
  failedWhy,
  busy,
  onPick,
  onOpen,
}: {
  index: number
  stage: PlanStage
  made: Artifact[]
  /** The result that goes on, workspace-relative ('' when nothing has been made). */
  picked: string
  hint: string
  rendering: boolean
  /** Why the last run failed, when the record carries it. */
  failedWhy: string
  busy: boolean
  onPick: (file: Artifact) => void
  onOpen: (file: Artifact) => void
}) {
  return (
    <section className="stg-results">
      <div className="stg-head">
        <div className="stg-head-text">
          <h3 className="stg-title">
            Stage {index + 1} · {words(stage.name)}
          </h3>
          <span className="stg-hint">{hint}</span>
        </div>
        {stage.facts.review && (
          <span className="stg-review" title="A review point: the next stage waits for your verdict">
            <Clock size={13} strokeWidth={2} /> You see it before the next stage
          </span>
        )}
      </div>

      {failedWhy && <div className="stg-fail">{failedWhy}</div>}

      {made.length > 0 ? (
        <div className="stg-grid">
          {made.map((a) => {
            const isPicked = !!picked && a.path.replace(/\\/g, '/').endsWith(picked)
            return (
              <div key={a.path} className={`stg-tile${isPicked ? ' is-picked' : ''}`}>
                <button
                  type="button"
                  className="stg-tile-pick"
                  disabled={busy}
                  title={isPicked ? 'Picked — this is what the next stage gets' : 'Pick this one'}
                  onClick={() => onPick(a)}
                >
                  <OutputThumb file={a} />
                </button>
                {isPicked && <span className="stg-picked-tag">Picked</span>}
                <span className="stg-tile-name st-mono">{a.name}</span>
                <button type="button" className="stg-tile-open" title="Open" aria-label={`Open ${a.name}`} onClick={() => onOpen(a)}>
                  <Eye size={13} strokeWidth={2} />
                </button>
              </div>
            )
          })}
          {rendering && (
            <div className="stg-tile is-rendering" aria-live="polite">
              <span className="stg-rendering-dot" />
              <span>Rendering on Comfy Cloud…</span>
            </div>
          )}
        </div>
      ) : (
        <div className={`stg-empty${rendering ? ' is-rendering' : ''}`}>
          {rendering ? (
            <>
              <span className="stg-rendering-dot" /> Rendering on Comfy Cloud…
            </>
          ) : (
            'Nothing made yet — press Run on the right.'
          )}
        </div>
      )}
    </section>
  )
}
