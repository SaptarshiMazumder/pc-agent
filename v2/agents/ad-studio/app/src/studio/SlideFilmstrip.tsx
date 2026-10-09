/* A post's slides in order, as a strip of thumbnails: click one to edit it beside the preview. Each
 * shows where it is in a design run that is moving (or what is still to fix after one). */

import { Film } from 'lucide-react'

import type { Slide, SlideDesignState } from '../agentd/posts'
import type { ViewerItem } from '../state/store'

export function SlideFilmstrip({
  slides,
  views,
  status,
  selected,
  onSelect,
}: {
  slides: Slide[]
  views: ViewerItem[]
  status: (i: number) => SlideDesignState | undefined
  selected: number
  onSelect: (i: number) => void
}) {
  return (
    <ol className="filmstrip" aria-label="Slides">
      {slides.map((s, i) => {
        const v = views[i]
        const st = status(i)
        return (
          <li key={`${s.item}:${i}`}>
            <button className={`film-slide${i === selected ? ' on' : ''}`} onClick={() => onSelect(i)} aria-current={i === selected} title={v.title}>
              {v.kind === 'video' ? <video src={v.src} muted playsInline preload="metadata" /> : <img src={v.src} alt="" loading="lazy" />}
              <span className="film-n">{i + 1}</span>
              {s.kind === 'video' && (
                <span className="film-kind">
                  <Film size={11} />
                </span>
              )}
              {st && <span className={`slide-design-state s-${st.state}`}>{st.state}</span>}
            </button>
          </li>
        )
      })}
    </ol>
  )
}
