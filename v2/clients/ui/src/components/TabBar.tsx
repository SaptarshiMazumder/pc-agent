import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Plus, ChevronDown, X } from 'lucide-react'

import { agentColor } from '../lib/agentPresentation'
import { whenLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import { useHoverTip } from './HoverTip'

/**
 * Chrome-style tab strip for the open chats.
 *  - horizontal scroll (scrollbar hidden via .tabbar-scroll::-webkit-scrollbar)
 *  - "+" opens a new chat, the chevron opens an overflow list of all open chats
 *  - tabs are draggable to reorder
 * Each tab carries its own agentId (dot colour + routing) and titles come from
 * the cross-agent tabTitles cache — so switching agents never blanks tab names.
 */
export default function TabBar() {
  const openTabs = useApp((s) => s.openTabs)
  const current = useApp((s) => s.currentSessionKey)
  const tabTitles = useApp((s) => s.tabTitles)
  const sessionRows = useApp((s) => s.sessionRows)
  const agents = useApp((s) => s.agents)
  const activateTab = useApp((s) => s.activateTab)
  const newSession = useApp((s) => s.newSession)
  const closeTab = useApp((s) => s.closeTab)
  const closeOtherTabs = useApp((s) => s.closeOtherTabs)
  const closeTabsToRight = useApp((s) => s.closeTabsToRight)
  const closeTabsToLeft = useApp((s) => s.closeTabsToLeft)
  const closeAllTabs = useApp((s) => s.closeAllTabs)
  const reorderTabs = useApp((s) => s.reorderTabs)

  const [menuOpen, setMenuOpen] = useState(false)
  // right-click context menu: which tab + where to render it (viewport coords)
  const [ctx, setCtx] = useState<{ id: string; x: number; y: number } | null>(null)
  // ids mid-close: kept in the DOM with .tab--closing so their collapse animation plays
  // before the store actually drops them (otherwise React removes the node instantly).
  const [closing, setClosing] = useState<Set<string>>(new Set())
  const dragged = useState<{ id: string | null }>({ id: null })[0]
  const menuWrapRef = useRef<HTMLDivElement>(null)
  const ctxRef = useRef<HTMLDivElement>(null)
  const tip = useHoverTip()

  // Close either open menu on an OUTSIDE click or Escape. A document-level mousedown
  // listener is used (not an overlay/backdrop) because the tab bar sits above scrolled
  // chat content whose own stacking wins over a fixed backdrop — so a backdrop never
  // reliably receives the click. mousedown fires before the menu items' click handlers,
  // and a click INSIDE the menu is ignored, so item actions still run.
  useEffect(() => {
    if (!menuOpen && !ctx) return
    const onDown = (e: MouseEvent): void => {
      const t = e.target as Node
      if (menuOpen && !menuWrapRef.current?.contains(t)) setMenuOpen(false)
      if (ctx && !ctxRef.current?.contains(t)) setCtx(null)
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') { setMenuOpen(false); setCtx(null) }
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [menuOpen, ctx])

  // ---- overflow scrolling: a custom overlay scrollbar (native one is hidden to keep the
  // active-tab↔chat merge intact) + wheel-to-scroll. The scrollbar shows on hover of the
  // strip only when the tabs actually overflow. -----------------------------------------
  const scrollRef = useRef<HTMLDivElement>(null)
  const [sb, setSb] = useState<{ show: boolean; w: number; left: number }>({ show: false, w: 0, left: 0 })

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

  // vertical wheel → horizontal scroll (native + non-passive so preventDefault sticks),
  // plus keep the custom scrollbar sized on resize
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

  // remeasure when the set of tabs (and thus total width) changes
  useEffect(() => {
    measure()
  }, [openTabs, tabTitles, measure])

  if (openTabs.length === 0) return null

  // drag the custom scrollbar thumb to scroll the strip
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

  const titleOf = (id: string): string => tabTitles[id] || 'New chat'
  // meta for the hover tooltip: msg count + last-activity date, from the saved-session row
  // (a brand-new/unsaved tab has no row yet → no meta line, just the name)
  const metaOf = (id: string): string | undefined => {
    const row = sessionRows.find((r) => r.sessionId === id)
    return row ? `${row.messages} msgs · ${whenLabel(row.modified * 1000)}` : undefined
  }
  // dot colour = the tab's own agent colour (server-assigned, falls back to a hash)
  const dotOf = (agentId: string): string =>
    agentColor(agents.find((a) => a.id === agentId)?.color, agentId)

  const beginClose = (id: string): void => {
    setClosing((s) => new Set(s).add(id))
    // let the .tab--closing collapse play, THEN remove from the store (matches the CSS .2s)
    setTimeout(() => {
      closeTab(id)
      setClosing((s) => {
        const next = new Set(s)
        next.delete(id)
        return next
      })
    }, 200)
  }

  return (
    <div className="tabbar">
      <div className="tabbar-clip">
        <div className="tabbar-scroll" ref={scrollRef} onScroll={measure}>
          {openTabs.map((tab) => (
            <div
              key={tab.id}
              className={`tab ${tab.id === current ? 'active' : ''} ${closing.has(tab.id) ? 'tab--closing' : ''} ${ctx?.id === tab.id ? 'tab--ctx' : ''}`}
              onClick={() => !closing.has(tab.id) && void activateTab(tab)}
              onContextMenu={(e) => {
                e.preventDefault()
                setMenuOpen(false)
                setCtx({ id: tab.id, x: Math.min(e.clientX, window.innerWidth - 212), y: e.clientY })
              }}
              draggable
              onDragStart={() => {
                dragged.id = tab.id
              }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                if (dragged.id) reorderTabs(dragged.id, tab.id)
                dragged.id = null
              }}
              {...tip.bind(titleOf(tab.id), metaOf(tab.id))}
            >
              <span className="tab-dot" style={{ background: dotOf(tab.agentId) }} />
              <span className="tab-title">{titleOf(tab.id)}</span>
              <button
                className="tab-close"
                title="close tab"
                onClick={(e) => {
                  e.stopPropagation()
                  beginClose(tab.id)
                }}
              >
                <X size={14} />
              </button>
            </div>
          ))}
          {/* the "+" sits right after the last tab and scrolls with them (Chrome-style) */}
          <button className="tab-add-inline" title="new chat" onClick={() => newSession()}>
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
        <button className="tabbar-add" title="all open chats" onClick={() => { setCtx(null); setMenuOpen((v) => !v) }}>
          <ChevronDown size={16} />
        </button>
        {menuOpen && (
          <div className="tab-menu">
            <div className="tab-menu-label">Open chats</div>
            <div className="tab-menu-list">
                {openTabs.map((tab) => (
                  <div key={tab.id} className={`tab-menu-item ${tab.id === current ? 'active' : ''}`}>
                    <button className="tab-menu-open" onClick={() => { void activateTab(tab); setMenuOpen(false) }}>
                      <span className="tab-dot" style={{ background: dotOf(tab.agentId) }} />
                      <span className="tab-title">{titleOf(tab.id)}</span>
                    </button>
                    <button className="tab-menu-close" title="close tab" onClick={(e) => { e.stopPropagation(); closeTab(tab.id) }}>
                      <X size={13} />
                    </button>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>

      {/* right-click (Chrome-style) tab context menu */}
      {ctx && (() => {
        const idx = openTabs.findIndex((t) => t.id === ctx.id)
        const run = (fn: () => void): void => { fn(); setCtx(null) }
        return (
          <div className="tab-ctx" style={{ left: ctx.x, top: ctx.y }} role="menu" ref={ctxRef}>
            <button className="tab-ctx-item" onClick={() => run(() => newSession())}>New chat</button>
            <div className="tab-ctx-sep" />
            <button className="tab-ctx-item" onClick={() => run(() => beginClose(ctx.id))}>Close</button>
            <button className="tab-ctx-item" disabled={openTabs.length <= 1} onClick={() => run(() => closeOtherTabs(ctx.id))}>Close other tabs</button>
            <button className="tab-ctx-item" disabled={idx <= 0} onClick={() => run(() => closeTabsToLeft(ctx.id))}>Close tabs to the left</button>
            <button className="tab-ctx-item" disabled={idx < 0 || idx >= openTabs.length - 1} onClick={() => run(() => closeTabsToRight(ctx.id))}>Close tabs to the right</button>
            <div className="tab-ctx-sep" />
            <button className="tab-ctx-item danger" onClick={() => run(() => closeAllTabs())}>Close all tabs</button>
          </div>
        )
      })()}
      {tip.node}
    </div>
  )
}
