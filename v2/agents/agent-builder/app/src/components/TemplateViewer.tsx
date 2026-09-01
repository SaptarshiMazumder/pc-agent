/* The template viewer — walk a template full-size before committing, Wix-style.
 *
 * THE THING IN THE FRAME IS REAL. The stage is an iframe of /template-previews/<id>/ — the
 * template compiled and served by the daemon, the same live imagery the launchpad shelf and the
 * old gallery scale down.
 *
 * THE FRAME OPENS FULL SCREEN ON CLICK, and in-frame clicking is deliberately off in the framed
 * view. The stage is only as big as this window allows, and a template wider than the stage
 * clips — walking a clipped app teaches the wrong lesson about it. So the frame is a doorway:
 * one click expands the preview to a full-window lightbox IN THE APP — every pixel this window
 * has, interior fully interactive, Escape steps back to the framed view. The header's
 * open-in-new-tab stays for whoever wants a real browser window.
 *
 * NO SCREENS RAIL, although the design draws one. Its rows would navigate the iframe to a named
 * screen, and the compiled templates read no screen parameter — there is nothing outside the
 * frame that can steer them. Rows that do nothing when clicked are worse than rows that are not
 * there, so the rail keeps only what is true standing still: the INCLUDED checklist (the four
 * shared screens every template really ships, from _common/) and the source path. The rail
 * grows the nav rows if the templates ever learn a ?screen= parameter.
 *
 * THE DEVICE SWITCHER is presentational by design: it sets the frame's width to a desktop,
 * tablet or phone footprint so the template's responsive behaviour is visible. Nothing is
 * emulated beyond the width.
 *
 * USE THIS TEMPLATE does not create anything — it has no name to create with. It closes the
 * viewer and opens the Blueprint with this template pre-picked, which is the same one-question
 * flow every other door leads to. Looking never commits; the Blueprint's Create does.
 */

import {
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  LayoutGrid,
  Maximize2,
  MessageSquare,
  Monitor,
  Smartphone,
  Tablet,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { TemplatePreviewLightbox } from './TemplatePreviewLightbox'
import { TEMPLATES } from './StartModal'

const ICONS: Record<string, ReactNode> = {
  chat: <MessageSquare size={16} />,
  dashboard: <LayoutGrid size={16} />,
}

/** The four screens every windowed template really ships — they come from _common/, which the
 *  scaffolder copies in whole, so this list is a fact about the product, not sample content. */
const INCLUDED = ['sign-in & accounts', 'credits & top-up', 'settings schema', 'organizations']

const DEVICE_WIDTHS: Record<string, string> = {
  desktop: '100%',
  tablet: '834px',
  phone: '390px',
}

export function TemplateViewer({
  templateId,
  onUse,
  onClose,
}: {
  templateId: string
  /** Carry the pick into the create flow — the Blueprint opens with this shape selected. */
  onUse: (templateId: string) => void
  onClose: () => void
}) {
  /** Which template the stage is showing — the switcher swaps it without leaving the viewer. */
  const [current, setCurrent] = useState(templateId)
  const [device, setDevice] = useState<'desktop' | 'tablet' | 'phone'>('desktop')
  /** The in-app lightbox: the preview at every pixel this window has. */
  const [full, setFull] = useState(false)

  const tpl = TEMPLATES.find((t) => t.id === current) || TEMPLATES[0]
  const at = TEMPLATES.findIndex((t) => t.id === tpl.id)

  // Escape unwinds one layer at a time: the lightbox first, then the viewer — closing
  // everything at once would throw away the framed view someone was just in.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      setFull((was) => {
        if (!was) onClose()
        return false
      })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const step = (dir: number): void => {
    const next = TEMPLATES[(at + dir + TEMPLATES.length) % TEMPLATES.length]
    if (next) setCurrent(next.id)
  }

  return (
    <div className="tv" role="dialog" aria-modal="true" aria-label={`Template preview — ${tpl.label}`}>
      <header className="tv-head">
        <button className="icon-btn" onClick={onClose} title="Back">
          <ChevronLeft size={17} />
        </button>
        <span className="tv-badge">{ICONS[tpl.id]}</span>
        <div className="tv-title">
          <span className="tv-name">{tpl.label}</span>
          <span className="tv-sub">
            <span className="live-dot" />
            live build · click it to open full screen
          </span>
        </div>
        <div className="tv-devices">
          {(
            [
              ['desktop', <Monitor size={15} key="d" />],
              ['tablet', <Tablet size={15} key="t" />],
              ['phone', <Smartphone size={15} key="p" />],
            ] as const
          ).map(([id, icon]) => (
            <button
              key={id}
              className={`tv-device ${device === id ? 'active' : ''}`}
              title={`${id} width`}
              aria-label={`${id} width`}
              onClick={() => setDevice(id)}
            >
              {icon}
            </button>
          ))}
        </div>
        <button
          className="icon-btn"
          title="Open in a new tab"
          aria-label="Open the preview in a new tab"
          onClick={() => window.open(`/template-previews/${tpl.id}/`, '_blank', 'noopener')}
        >
          <ExternalLink size={16} />
        </button>
        <button className="prime-btn" onClick={() => onUse(tpl.id)}>
          Use this template
          <ArrowRight size={15} />
        </button>
        <button className="icon-btn" onClick={onClose} title="Close (Esc)">
          <X size={16} />
        </button>
      </header>

      <div className="tv-body">
        <aside className="tv-rail">
          <span className="tv-rail-label">Included</span>
          <ul className="tv-included">
            {INCLUDED.map((f) => (
              <li key={f}>
                <Check size={14} />
                {f}
              </li>
            ))}
          </ul>
          <code className="tv-source">templates/_variants/{tpl.id}</code>
        </aside>

        <div className="tv-stage">
          <div className="tv-frame-wrap" style={{ width: DEVICE_WIDTHS[device] }}>
            {/* keyed so switching templates reloads cleanly rather than navigating a stale app */}
            <iframe
              key={tpl.id}
              className="tv-frame"
              src={`/template-previews/${tpl.id}/`}
              title={`${tpl.label} preview`}
            />
            {/* The doorway — see the header. Covers the frame, so a click cannot half-land
                inside a clipped app; the pill says where the door goes. */}
            <button
              className="tv-frame-open"
              title={`Open ${tpl.label} full screen`}
              aria-label={`Open ${tpl.label} full screen`}
              onClick={() => setFull(true)}
            >
              <span className="tv-frame-open-pill">
                <Maximize2 size={14} />
                Open full screen
              </span>
            </button>
          </div>

          <div className="tv-switcher">
            <button className="icon-btn" onClick={() => step(-1)} title="Previous template">
              <ChevronLeft size={15} />
            </button>
            {TEMPLATES.map((t) => (
              <button
                key={t.id}
                className={`tv-thumb ${t.id === tpl.id ? 'active' : ''}`}
                title={t.blurb}
                onClick={() => setCurrent(t.id)}
              >
                {/* The clipping box. `transform: scale` shrinks pixels, not the layout box —
                    without this wrapper each thumbnail stands 545px tall in the row and the
                    switcher eats the stage. Same move as .lp-tpl-thumb. */}
                <span className="tv-thumb-shot">
                  <iframe
                    className="tv-thumb-frame"
                    src={`/template-previews/${t.id}/`}
                    title={`${t.label} thumbnail`}
                    tabIndex={-1}
                  />
                </span>
                <span className="tv-thumb-caption">{t.label}</span>
              </button>
            ))}
            <button className="icon-btn" onClick={() => step(1)} title="Next template">
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      </div>

      {/* The lightbox: the same preview, every pixel this window has, fully interactive —
          nothing is clipped, so interior clicking means something again. Escape or ✕ steps
          back to the framed view; the viewer underneath keeps its state. */}
      {full && (
        <TemplatePreviewLightbox templateId={tpl.id} onUse={onUse} onClose={() => setFull(false)} />
      )}
    </div>
  )
}
