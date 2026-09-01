import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { ChevronDown, Plus, X } from 'lucide-react'

import { agentColor } from '../lib/agentPresentation'
import { whenLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import { useHoverTip } from './HoverTip'

/**
 * The open conversations, as a Chrome-style tab strip — COPIED FROM agentd's TabBar.
 *
 *  - horizontal scroll with the native scrollbar hidden, replaced by an overlay one that appears
 *    on hover only when the tabs actually overflow
 *  - "+" opens a new conversation; the chevron opens a list of every open one
 *  - tabs drag to reorder, and right-click for the close-others / left / right / all menu
 *
 * WHAT IS DIFFERENT FROM agentd's: its tabs carry an `agentId` each, because that window talks to
 * every agent on the machine and the dot says which one a tab belongs to. Every conversation here
 * belongs to Agent Builder, so the dot is hashed from the SESSION instead — a conversation keeps
 * its colour across restarts, which is what makes a strip of near-identical "New chat" titles
 * scannable at all.
 */
export default function TabBar() {
  const openTabs = useApp((s) => s.openTabs)
  const current = useApp((s) => s.currentSessionKey)
  const chats = useApp((s) => s.chats)
  const activateTab = useApp((s) => s.activateTab)
  const closeTab = useApp((s) => s.closeTab)
  const setView = useApp((s) => s.setView)
  const reorderTabs = useApp((s) => s.reorderTabs)
  const closeOthers = useApp((s) => s.closeOthers)
  const closeToLeft = useApp((s) => s.closeToLeft)
  const closeToRight = useApp((s) => s.closeToRight)
  const closeAll = useApp((s) => s.closeAll)

  const [menuOpen, setMenuOpen] = useState(false)
  // right-click menu: which tab, and where to draw it (viewport coords)
  const [ctx, setCtx] = useState<{ key: string; x: number; y: number } | null>(null)
  // keys mid-close: kept in the DOM with .tab--closing so the collapse animation plays before the
  // store actually drops them (otherwise React removes the node instantly and it just vanishes)
  const [closing, setClosing] = useState<Set<string>>(new Set())
  const dragged = useState<{ key: string | null }>({ key: null })[0]
  const menuWrapRef = useRef<HTMLDivElement>(null)
  const ctxRef = useRef<HTMLDivElement>(null)
  const tip = useHoverTip()

  /** A tab's name comes from the saved-session row. A conversation nobody has spoken in yet has no
   *  row and no title — the daemon writes one from the first exchange — so it reads "New chat"
   *  until it has earned a name. */
  const rowOf = (key: string) => chats.find((c) => c.sessionId === key)
  const titleOf = (key: string): string => rowOf(key)?.title || 'New chat'
  const metaOf = (key: string): string | undefined => {
    const row = rowOf(key)
    if (!row) return undefined
    return [row.messages ? `${row.messages} msgs` : '', whenLabel((row.modified || 0) * 1000)]
      .filter(Boolean)
      .join(' · ')
  }

  /* Close either menu on an OUTSIDE click or Escape. A document-level mousedown listener rather
     than a backdrop, because the strip sits above scrolled content whose own stacking wins over a
     fixed backdrop — so a backdrop never reliably receives the click. mousedown fires before the
     menu items' click handlers, and a click INSIDE the menu is ignored, so items still run. */
  useEffect(() => {
    if (!menuOpen && !ctx) return
    const onDown = (e: MouseEvent): void => {
      const t = e.target as Node
      if (menuOpen && !menuWrapRef.current?.contains(t)) setMenuOpen(false)
      if (ctx && !ctxRef.current?.contains(t)) setCtx(null)
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        setMenuOpen(false)
        setCtx(null)
      }
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [menuOpen, ctx])

  /* ---- overflow: a custom overlay scrollbar (the native one is hidden to keep the active-tab ↔
     conversation merge intact) plus wheel-to-scroll. It shows on hover of the strip only when the
     tabs genuinely overflow. ------------------------------------------------------------- */
  const scrollRef = useRef<HTMLDivElement>(null)
  const [sb, setSb] = useState<{ show: boolean; w: number; left: number }>({
    show: false,
    w: 0,
    left: 0,
  })

  const measure = useCallback((): void => {
    const el = scrollRef.current
    if (!el) return
    const { scrollWidth, clientWidth, scrollLeft } = el
    if (scrollWidth <= clientWidth + 1) {
      setSb((s) => (s.show ? { show: false, w: 0, left: 0 } : s))
      return
    }
    const track = clientWidth
    const w = Math.max(28, (clientWidth / scrollWidth) * track)
    const max = scrollWidth - clientWidth
    const left = max > 0 ? (scrollLeft / max) * (track - w) : 0
    setSb({ show: true, w, left })
  }, [])

  // vertical wheel -> horizontal scroll (native and non-passive so preventDefault sticks), plus
  // keeping the custom scrollbar sized on resize
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onWheel = (e: WheelEvent): void => {
      if (el.scrollWidth <= el.clientWidth) return
      const delta = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX
      if (!delta) return
      el.scrollLeft += delta
      e.preventDefault()
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    const ro = new ResizeObserver(() => measure())
    ro.observe(el)
    measure()
    return () => {
      el.removeEventListener('wheel', onWheel)
      ro.disconnect()
    }
  }, [measure])

  // remeasure when the set of tabs (and so the total width) changes
  useEffect(() => {
    measure()
  }, [openTabs, chats, measure])

  // drag the thumb to scroll the strip
  const onThumbDown = (e: ReactPointerEvent): void => {
    e.preventDefault()
    e.stopPropagation()
    const el = scrollRef.current
    if (!el) return
    const startX = e.clientX
    const startScroll = el.scrollLeft
    const track = el.clientWidth
    const w = sb.w
    const max = el.scrollWidth - el.clientWidth
    const onMove = (ev: PointerEvent): void => {
      const dx = ev.clientX - startX
      el.scrollLeft = startScroll + (track - w > 0 ? (dx / (track - w)) * max : 0)
      measure()
    }
    const onUp = (): void => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  /** Play the collapse, THEN drop it from the store. Removing it first takes the node with it and
   *  the tab simply disappears. */
  const beginClose = (key: string): void => {
    setClosing((s) => new Set(s).add(key))
    setTimeout(() => {
      closeTab(key)
      setClosing((s) => {
        const next = new Set(s)
        next.delete(key)
        return next
      })
    }, 200) // matches the .tab--closing transition
  }

  return (
    <div className="tabbar">
      <div className="tabbar-clip">
        <div className="tabbar-scroll" ref={scrollRef} onScroll={measure}>
          {openTabs.map((key) => (
            <div
              key={key}
              className={`tab ${key === current ? 'active' : ''} ${closing.has(key) ? 'tab--closing' : ''} ${ctx?.key === key ? 'tab--ctx' : ''}`}
              onClick={() => !closing.has(key) && activateTab(key)}
              onContextMenu={(e) => {
                e.preventDefault()
                setMenuOpen(false)
                setCtx({ key, x: Math.min(e.clientX, window.innerWidth - 212), y: e.clientY })
              }}
              draggable
              onDragStart={() => {
                dragged.key = key
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                if (dragged.key) reorderTabs(dragged.key, key)
                dragged.key = null
              }}
              {...tip.bind(titleOf(key), metaOf(key))}
            >
              <span className="tab-dot" style={{ background: agentColor(undefined, key) }} />
              <span className="tab-title">{titleOf(key)}</span>
              <button
                className="tab-close"
                title="Close tab"
                aria-label="Close tab"
                onClick={(e) => {
                  e.stopPropagation()
                  beginClose(key)
                }}
              >
                <X size={14} />
              </button>
            </div>
          ))}
          {/* the "+" sits right after the last tab and scrolls with them, Chrome-style */}
          <button className="tab-add-inline" title="Start something — opens the Launchpad" onClick={() => setView('launchpad')}>
            <Plus size={16} />
          </button>
        </div>
        {sb.show && (
          <div className="tab-scrollbar" aria-hidden>
            <div
              className="tab-scrollbar-thumb"
              style={{ width: sb.w, left: sb.left }}
              onPointerDown={onThumbDown}
            />
          </div>
        )}
      </div>

      <div className="tabbar-menu-wrap" ref={menuWrapRef}>
        <button
          className="tabbar-add"
          title="All open conversations"
          onClick={() => {
            setCtx(null)
            setMenuOpen((v) => !v)
          }}
        >
          <ChevronDown size={16} />
        </button>
        {menuOpen && (
          <div className="tab-menu">
            <div className="tab-menu-label">Open conversations</div>
            <div className="tab-menu-list">
              {openTabs.map((key) => (
                <div key={key} className={`tab-menu-item ${key === current ? 'active' : ''}`}>
                  <button
                    className="tab-menu-open"
                    onClick={() => {
                      activateTab(key)
                      setMenuOpen(false)
                    }}
                  >
                    <span className="tab-dot" style={{ background: agentColor(undefined, key) }} />
                    <span className="tab-title">{titleOf(key)}</span>
                  </button>
                  <button
                    className="tab-menu-close"
                    title="Close tab"
                aria-label="Close tab"
                    onClick={(e) => {
                      e.stopPropagation()
                      closeTab(key)
                    }}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* right-click (Chrome-style) tab menu */}
      {ctx &&
        (() => {
          const at = openTabs.indexOf(ctx.key)
          const run = (fn: () => void): void => {
            fn()
            setCtx(null)
          }
          return (
            <div className="tab-ctx" style={{ left: ctx.x, top: ctx.y }} role="menu" ref={ctxRef}>
              <button className="tab-ctx-item" onClick={() => run(() => setView('launchpad'))}>
                Start something…
              </button>
              <div className="tab-ctx-sep" />
              <button className="tab-ctx-item" onClick={() => run(() => beginClose(ctx.key))}>
                Close
              </button>
              <button
                className="tab-ctx-item"
                disabled={openTabs.length <= 1}
                onClick={() => run(() => closeOthers(ctx.key))}
              >
                Close other tabs
              </button>
              <button
                className="tab-ctx-item"
                disabled={at <= 0}
                onClick={() => run(() => closeToLeft(ctx.key))}
              >
                Close tabs to the left
              </button>
              <button
                className="tab-ctx-item"
                disabled={at < 0 || at >= openTabs.length - 1}
                onClick={() => run(() => closeToRight(ctx.key))}
              >
                Close tabs to the right
              </button>
              <div className="tab-ctx-sep" />
              <button className="tab-ctx-item danger" onClick={() => run(() => closeAll())}>
                Close all tabs
              </button>
            </div>
          )
        })()}
      {tip.node}
    </div>
  )
}
