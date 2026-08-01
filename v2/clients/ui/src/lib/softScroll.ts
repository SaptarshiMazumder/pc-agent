/**
 * Global "soft scroll edges" — the whole app, automatically.
 *
 * Every scrollable container (present or added later, on any page) gets a mask that fades
 * its content out wherever a scroll edge is currently clipping it — the Gemini look — so a
 * new view never has to opt in and we never wire it per-component. Detection is by computed
 * `overflow` (auto/scroll) via an initial scan + a MutationObserver, and the fade is
 * recomputed on scroll / resize / content change.
 *
 * The fade is DYNAMIC: an edge only fades while it actually hides content. That has two nice
 * consequences — content is never dimmed at rest, and a top-pinned sticky header (e.g. the
 * Settings header/nav, or the CSV viewer's column headers) is never faded, because we
 * suppress the top fade on any scroller that contains a `position: sticky` child. (The
 * sidebar's PROJECTS/CHATS labels are intentionally NOT sticky so that list fades too.)
 *
 * A single-axis mask is emitted (vertical when a vertical edge clips, else horizontal) so we
 * avoid `mask-composite` entirely — one `mask-image` layer, universally supported in Chromium.
 *
 * Opt out of a specific container by giving it `data-no-soft-scroll`.
 */

const FADE = 26 // px transparent runway at a clipped edge
const EPS = 2 // slack so sub-pixel rounding doesn't flicker the fade on/off
const SCROLL_HIDE_MS = 700 // how long the scrollbar stays visible after the last scroll tick

export function installSoftScroll(): () => void {
  const managed = new Set<HTMLElement>()
  const seen = new WeakSet<Element>() // elements already tested for overflow (avoid re-reading style)
  const hideTimers = new WeakMap<HTMLElement, ReturnType<typeof setTimeout>>()

  // reveal the scrollbar (via [data-scrolling]) only while scrolling, then hide it again
  const flagScrolling = (el: HTMLElement): void => {
    el.setAttribute('data-scrolling', '')
    const prev = hideTimers.get(el)
    if (prev) clearTimeout(prev)
    hideTimers.set(
      el,
      setTimeout(() => {
        el.removeAttribute('data-scrolling')
        hideTimers.delete(el)
      }, SCROLL_HIDE_MS)
    )
  }

  const ro = new ResizeObserver(() => schedule())

  let raf = 0
  const schedule = (): void => {
    if (raf) return
    raf = requestAnimationFrame(() => {
      raf = 0
      for (const el of managed) apply(el)
    })
  }

  /** Compute & set the mask for one scroller (or drop it if detached / nothing clipped). */
  const apply = (el: HTMLElement): void => {
    if (!el.isConnected) {
      ro.unobserve(el)
      managed.delete(el)
      return
    }
    const sticky = el.dataset.softSticky != null
    const top = !sticky && el.scrollTop > EPS
    const bot = el.scrollTop + el.clientHeight < el.scrollHeight - EPS
    const left = el.scrollLeft > EPS
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - EPS

    let mask = ''
    if (top || bot) {
      mask = `linear-gradient(to bottom, transparent 0, #000 ${top ? FADE : 0}px, #000 calc(100% - ${bot ? FADE : 0}px), transparent 100%)`
    } else if (left || right) {
      mask = `linear-gradient(to right, transparent 0, #000 ${left ? FADE : 0}px, #000 calc(100% - ${right ? FADE : 0}px), transparent 100%)`
    }

    if (!mask) {
      el.style.removeProperty('--soft-mask')
      el.removeAttribute('data-soft-scroll')
      return
    }
    el.style.setProperty('--soft-mask', mask)
    el.setAttribute('data-soft-scroll', '')
  }

  /** Adopt an element iff it is a scroll container we don't already manage. */
  const adopt = (el: HTMLElement): void => {
    if (seen.has(el)) return
    seen.add(el)
    if (el.dataset.noSoftScroll != null) return
    const cs = getComputedStyle(el)
    const scrolls = /auto|scroll/.test(cs.overflowY) || /auto|scroll/.test(cs.overflowX)
    if (!scrolls) return
    managed.add(el)
    // a top-pinned sticky child must not be dimmed by the top fade
    for (const child of el.querySelectorAll('*')) {
      if (getComputedStyle(child).position === 'sticky') {
        el.dataset.softSticky = ''
        break
      }
    }
    ro.observe(el)
  }

  const scan = (root: Element): void => {
    adopt(root as HTMLElement)
    for (const el of root.querySelectorAll<HTMLElement>('*')) adopt(el)
  }

  scan(document.body)
  schedule()

  // pick up containers rendered later (route changes, new lists, streamed content …)
  const mo = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) if (n instanceof HTMLElement) scan(n)
    }
    schedule() // content/size may also have changed inside existing scrollers
  })
  mo.observe(document.body, { childList: true, subtree: true })

  // scroll doesn't bubble → listen in the capture phase for every scroller at once
  const onScroll = (e: Event): void => {
    if (e.target instanceof HTMLElement && managed.has(e.target)) {
      flagScrolling(e.target)
      schedule()
    }
  }
  document.addEventListener('scroll', onScroll, { capture: true, passive: true })

  return () => {
    document.removeEventListener('scroll', onScroll, { capture: true } as EventListenerOptions)
    mo.disconnect()
    ro.disconnect()
    if (raf) cancelAnimationFrame(raf)
    for (const el of managed) {
      const t = hideTimers.get(el)
      if (t) clearTimeout(t)
      el.removeAttribute('data-soft-scroll')
      el.removeAttribute('data-scrolling')
      delete el.dataset.softSticky
      el.style.removeProperty('--soft-mask')
    }
    managed.clear()
  }
}
