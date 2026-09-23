/* UseCaseCarousel — the shelf of things to make, as one swipeable row.
 *
 * WHY A CARROUSEL AND NOT A GRID. Six cards stacked is a long scroll on a phone and the sixth
 * is never reached; six across is a wall on a desktop. A row you push through shows that there
 * are more without spending the page on them, which is what a shelf is for.
 *
 * THE SCROLLER IS NATIVE. `overflow-x: auto` with `scroll-snap-type` — so a trackpad, a
 * touchscreen, a shift-wheel and the keyboard all work already, and the arrows are a convenience
 * over the top rather than the only way through. A carousel that only responds to its own two
 * buttons is a carousel that cannot be swiped, which is how most people will try first.
 *
 * NO AUTOPLAY. Content that moves on its own steals the reader's place and is the single most
 * complained-about pattern on a marketing page. It moves when somebody moves it.
 */

import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { LandingShot } from './LandingShot'

export type UseCase = { title: string; body: string; shot: string }

export function UseCaseCarousel({ items }: { items: UseCase[] }): JSX.Element {
  const track = useRef<HTMLDivElement | null>(null)
  const [atStart, setAtStart] = useState(true)
  const [atEnd, setAtEnd] = useState(false)

  /** Which arrows are useful right now. An arrow that does nothing is worse than no arrow. */
  const measure = useCallback(() => {
    const el = track.current
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    setAtStart(el.scrollLeft <= 2)
    // A pixel of slack: fractional widths mean scrollLeft rarely lands exactly on max.
    setAtEnd(el.scrollLeft >= max - 2)
  }, [])

  useEffect(() => {
    const el = track.current
    if (!el) return
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    window.addEventListener('resize', measure)
    return () => {
      el.removeEventListener('scroll', measure)
      window.removeEventListener('resize', measure)
    }
  }, [measure])

  const nudge = (dir: 1 | -1) => {
    const el = track.current
    if (!el) return
    // ONE CARD AT A TIME, measured from the first child rather than assumed: the card width is a
    // clamp against the viewport, so hardcoding it here would drift on every screen but mine.
    const card = el.firstElementChild as HTMLElement | null
    const step = card ? card.offsetWidth + 18 : el.clientWidth * 0.8
    el.scrollBy({ left: step * dir, behavior: 'smooth' })
  }

  return (
    <div className="lp-carousel">
      <div className="lp-carousel-track" ref={track} role="region" aria-label="What people make">
        {items.map((u, i) => (
          <article key={u.title} className="lp-card">
            <LandingShot src={u.shot} alt={u.title} tone={i} className="lp-card-shot" />
            <h3 className="lp-card-t">{u.title}</h3>
            <p className="lp-card-b">{u.body}</p>
          </article>
        ))}
      </div>

      {/* Below the row rather than over it: arrows floating on top of the first and last card
          cover the thing they are meant to help you read, and on a phone they land under a
          thumb that is already trying to swipe. */}
      <div className="lp-carousel-nav">
        <button
          className="lp-carousel-btn"
          onClick={() => nudge(-1)}
          disabled={atStart}
          aria-label="Previous"
        >
          <ChevronLeft size={18} strokeWidth={2} />
        </button>
        <button
          className="lp-carousel-btn"
          onClick={() => nudge(1)}
          disabled={atEnd}
          aria-label="Next"
        >
          <ChevronRight size={18} strokeWidth={2} />
        </button>
      </div>
    </div>
  )
}

export default UseCaseCarousel
