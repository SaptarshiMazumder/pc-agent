import { useEffect, useRef } from 'react'

/**
 * Adds `is-visible` to the element the first time it scrolls into view, which
 * fires the `.reveal` transition in base.css. One observer per element,
 * disconnected after it fires — reveals never replay on scroll-back.
 *
 * Reduced-motion users are served by the CSS, which renders `.reveal` in its
 * final state regardless of this class.
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(threshold = 0.16) {
  const ref = useRef<T | null>(null)

  useEffect(() => {
    const node = ref.current
    if (!node) return

    // No IntersectionObserver (or a test env): show the content rather than hide it.
    if (typeof IntersectionObserver === 'undefined') {
      node.classList.add('is-visible')
      return
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          entry.target.classList.add('is-visible')
          observer.disconnect()
        }
      },
      { threshold, rootMargin: '0px 0px -8% 0px' },
    )

    observer.observe(node)
    return () => observer.disconnect()
  }, [threshold])

  return ref
}
