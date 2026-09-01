/* The full-window template preview — the same lightbox wherever a template can be looked at.
 *
 * One implementation, two hosts: the template viewer's framed stage opens it, and the
 * Blueprint's shape cards open it. Extracted so "preview a template full size" cannot mean two
 * different things in two places.
 *
 * DELIBERATELY DUMB: no Escape listener of its own. Both hosts already run a layered Escape
 * (unwind the lightbox first, then the page), and a second listener here would double-fire and
 * close both layers on one press. The host closes it; this renders it.
 */

import { ArrowRight, LayoutGrid, MessageSquare, X } from 'lucide-react'
import type { ReactNode } from 'react'

import { TEMPLATES } from './StartModal'

const ICONS: Record<string, ReactNode> = {
  chat: <MessageSquare size={16} />,
  dashboard: <LayoutGrid size={16} />,
}

export function TemplatePreviewLightbox({
  templateId,
  onUse,
  onClose,
}: {
  templateId: string
  /** "Use this template" in the bar — absent when the host has no use for a commitment here. */
  onUse?: (templateId: string) => void
  onClose: () => void
}) {
  const tpl = TEMPLATES.find((t) => t.id === templateId) || TEMPLATES[0]

  return (
    <div className="tv-full" role="dialog" aria-modal="true" aria-label={`${tpl.label} — full screen`}>
      <div className="tv-full-bar">
        <span className="tv-badge">{ICONS[tpl.id]}</span>
        <span className="tv-full-name">{tpl.label}</span>
        {onUse && (
          <button className="prime-btn" onClick={() => onUse(tpl.id)}>
            Use this template
            <ArrowRight size={15} />
          </button>
        )}
        <button className="icon-btn" onClick={onClose} title="Close (Esc)">
          <X size={16} />
        </button>
      </div>
      <iframe
        key={`full-${tpl.id}`}
        className="tv-full-frame"
        src={`/template-previews/${tpl.id}/`}
        title={`${tpl.label} — full screen`}
      />
    </div>
  )
}
