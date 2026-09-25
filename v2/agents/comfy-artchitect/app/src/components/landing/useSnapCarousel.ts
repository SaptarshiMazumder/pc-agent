/* The landing page's sideways carousels — the render reel and the pricing cards — share this.
 *
 * A NATIVE SCROLLER, MADE USABLE WITH A MOUSE. The track is `overflow-x: auto` with scroll-snap,
 * so swipe, trackpad and shift-wheel already work; this adds what a mouse lacks once the
 * scrollbar is hidden: prev/next (most of a screen per press, disabled at each end) and
 * click-and-drag (mouse only — touch already scrolls natively). No auto-advance.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export function useSnapCarousel<T extends HTMLElement>() {
  const track = useRef<T | null>(null)
  const [atStart, setAtStart] = useState(true)
  const [atEnd, setAtEnd] = useState(false)

  /** Which arrows are useful right now — an arrow that does nothing is worse than none. */
  const measure = useCallback(() => {
    const el = track.current
    if (!el) return
    setAtStart(el.scrollLeft <= 2)
    // A pixel of slack: fractional widths mean scrollLeft rarely lands exactly on the max.
    setAtEnd(el.scrollLeft >= el.scrollWidth - el.clientWidth - 2)
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

  /** Most of a screen at a time, so the next press shows new cards, not one more sliver. */
  const nudge = (dir: 1 | -1) => {
    const el = track.current
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: 'smooth' })
  }

  /* Snapping is suspended while dragging, or the browser fights the pointer back to the nearest
     card on every move. */
  const drag = useRef<{ x: number; left: number } | null>(null)
  const dragHandlers = {
    onPointerDown: (e: React.PointerEvent<T>) => {
      if (e.pointerType !== 'mouse' || e.button !== 0) return
      const el = track.current
      if (!el) return
      drag.current = { x: e.clientX, left: el.scrollLeft }
      el.classList.add('is-dragging')
    },
    onPointerMove: (e: React.PointerEvent<T>) => {
      const d = drag.current
      const el = track.current
      if (d && el) el.scrollLeft = d.left - (e.clientX - d.x)
    },
    onPointerUp: () => {
      track.current?.classList.remove('is-dragging')
      drag.current = null
    },
    onPointerLeave: () => {
      track.current?.classList.remove('is-dragging')
      drag.current = null
    },
    // A drag must not end in an image being dragged out of the page.
    onDragStart: (e: React.DragEvent<T>) => e.preventDefault(),
  }

  return { track, atStart, atEnd, nudge, dragHandlers }
}
