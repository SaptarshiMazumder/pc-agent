/* One "what goes in → what comes out" row: the inputs, an arrow, each output in turn.
 *
 * ONE ROW, TWO PLACES. The landing page's real examples and the Templates page's cards draw
 * this same row, so a template is shown exactly the way the product is sold: these photos went
 * in, this post and this reel came out. `children` is whatever the screen adds under it (a
 * template's inputs and its Use button).
 */

import type { ReactNode } from 'react'
import { ArrowRight } from 'lucide-react'

import { LandingShot } from './LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'
import type { Pipeline } from './landing-content'

import './landing.css'

export function LandingPipelineRow({ pipeline: p, children }: { pipeline: Pipeline; children?: ReactNode }) {
  return (
    <article className="lp-pipe">
      <div className="lp-pipe-head">
        <h3 className="lp-pipe-title">{p.title}</h3>
        <p className="lp-pipe-prompt">&ldquo;{p.prompt}&rdquo;</p>
        {children}
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
  )
}

export default LandingPipelineRow
