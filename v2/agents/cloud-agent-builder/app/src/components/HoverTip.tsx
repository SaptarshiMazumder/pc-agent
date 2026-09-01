import { useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * A lightweight hover tooltip that renders through a portal (so it's never clipped by a
 * scroll container's overflow/mask) and shows a bold title + an optional meta line.
 *
 * Usage — call the hook once per component, spread `bind(title, sub)` on each trigger, and
 * render `node` once:
 *   const tip = useHoverTip()
 *   <button {...tip.bind('Full name', '12 msgs · Today')}>…</button>
 *   {tip.node}
 * `bind` is a plain factory (not a hook), so it's safe to call inside a .map().
 */
type Tip = { rect: DOMRect; title: string; sub?: string }

export function useHoverTip(delay = 350): {
  bind: (title: string, sub?: string) => { onMouseEnter: (e: MouseEvent<HTMLElement>) => void; onMouseLeave: () => void }
  node: ReactNode
  hide: () => void
} {
  const [tip, setTip] = useState<Tip | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  // cancel a pending show AND drop any visible tip — used e.g. when a menu opens so the
  // tooltip can't cover it
  const hide = (): void => {
    if (timer.current) clearTimeout(timer.current)
    setTip(null)
  }

  const bind = (title: string, sub?: string) => ({
    onMouseEnter: (e: MouseEvent<HTMLElement>): void => {
      const el = e.currentTarget
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setTip({ rect: el.getBoundingClientRect(), title, sub }), delay)
    },
    onMouseLeave: hide
  })

  const node = tip ? createPortal(<TipCard tip={tip} />, document.body) : null
  return { bind, node, hide }
}

function TipCard({ tip }: { tip: Tip }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: tip.rect.left, top: tip.rect.bottom + 6 })

  // clamp into the viewport (and flip above the trigger if it would overflow the bottom)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const w = el.offsetWidth
    const h = el.offsetHeight
    const pad = 8
    let left = tip.rect.left
    let top = tip.rect.bottom + 6
    if (left + w > window.innerWidth - pad) left = window.innerWidth - pad - w
    if (left < pad) left = pad
    if (top + h > window.innerHeight - pad) top = tip.rect.top - h - 6
    setPos({ left, top })
  }, [tip])

  return (
    <div ref={ref} className="hovertip" style={{ left: pos.left, top: pos.top }}>
      <div className="hovertip-title">{tip.title}</div>
      {tip.sub && <div className="hovertip-sub">{tip.sub}</div>}
    </div>
  )
}
