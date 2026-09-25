/* The proof reel: a sideways carousel of real renders under the hero, at their own widths.
 *
 * IT REPLACED A MARQUEE OF MODEL NAMES. A list of model names tells a visitor what the tool is
 * built from; a row of pictures tells them what they will get, which is the only thing they came
 * to find out. Arrows and click-and-drag come from useSnapCarousel; clips inside play only while
 * visible (LandingShot). No auto-advance — content that moves by itself steals the reader's place.
 */

import { ChevronLeft, ChevronRight } from 'lucide-react'

import { LandingShot } from './LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'
import { useSnapCarousel } from './useSnapCarousel'
import type { ReelShot } from './landing-content'

export function LandingRenderReel({ shots }: { shots: ReelShot[] }): JSX.Element {
  const { track, atStart, atEnd, nudge, dragHandlers } = useSnapCarousel<HTMLDivElement>()

  return (
    <section className="lp-reel" aria-label="Made with Comfy Penguin">
      <div
        className={`lp-reel-track${atStart ? ' at-start' : ''}${atEnd ? ' at-end' : ''}`}
        ref={track}
        {...dragHandlers}
      >
        {shots.map((s) => (
          <figure key={s.src} className="lp-tile" style={{ width: s.width }}>
            <LandingShot src={s.src} alt={s.alt} className="shot-fill" />
            {s.kind === 'video' && (
              <span className="lp-tile-tl">
                <MediaKindTag kind="video" over />
              </span>
            )}
            {s.label && <span className="lp-tile-br">{s.label}</span>}
          </figure>
        ))}
      </div>
      <button type="button" className="lp-reel-btn is-prev" onClick={() => nudge(-1)} disabled={atStart} aria-label="Previous renders">
        <ChevronLeft size={20} strokeWidth={2.2} />
      </button>
      <button type="button" className="lp-reel-btn is-next" onClick={() => nudge(1)} disabled={atEnd} aria-label="More renders">
        <ChevronRight size={20} strokeWidth={2.2} />
      </button>
    </section>
  )
}

export default LandingRenderReel
