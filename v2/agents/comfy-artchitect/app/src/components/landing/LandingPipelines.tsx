/* "Watch a workflow work" — real pipelines, end to end: what went in, and each thing that came out.
 *
 * WHY THIS SECTION. A grid of pretty outputs says "it can make pictures"; so does every tool.
 * Showing the INPUTS beside the outputs — this jacket and this photo became this selfie and this
 * clip — is what says "it builds a pipeline", and the second row (the same workflow, new garment,
 * new model) is the reuse argument made with pictures instead of words.
 */

import { ArrowRight } from 'lucide-react'

import { LandingShot } from './LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'
import type { Pipeline } from './landing-content'

export function LandingPipelines({ pipelines }: { pipelines: Pipeline[] }): JSX.Element {
  return (
    <div className="lp-pipes">
      {pipelines.map((p) => (
        <article key={p.title} className="lp-pipe">
          <div className="lp-pipe-head">
            <h3 className="lp-pipe-title">{p.title}</h3>
            <p className="lp-pipe-prompt">&ldquo;{p.prompt}&rdquo;</p>
          </div>
          <div className="lp-pipe-row">
            <div className="lp-pipe-inputs">
              {p.inputs.map((s) => (
                <figure key={s.src} className="lp-pipe-in">
                  <LandingShot src={s.src} alt={s.label} className="shot-fill" />
                  <figcaption>{s.label}</figcaption>
                </figure>
              ))}
            </div>
            <span className="lp-pipe-arrow" aria-hidden="true">
              <ArrowRight size={18} strokeWidth={2.2} />
            </span>
            {p.outputs.map((s, i) => (
              <div key={s.src} className="lp-pipe-step">
                {i > 0 && (
                  <span className="lp-pipe-arrow" aria-hidden="true">
                    <ArrowRight size={18} strokeWidth={2.2} />
                  </span>
                )}
                <figure className="lp-pipe-out">
                  <LandingShot src={s.src} alt={s.label} className="shot-fill" />
                  {s.kind !== 'input' && (
                    <span className="lp-tile-tl">
                      <MediaKindTag kind={s.kind} over />
                    </span>
                  )}
                  <figcaption>{s.label}</figcaption>
                </figure>
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  )
}

export default LandingPipelines
