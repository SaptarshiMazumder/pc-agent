/* The launchpad's template shelf — pick a shape to start from, before there is anything to say.
 *
 * THE CARDS ARE THE REAL TEMPLATES. Each windowed card's thumbnail is the compiled template
 * itself, served at /template-previews/<id>/ and scaled down — the same live-iframe move the
 * Start dialog's gallery makes, for the same reason: imagery that cannot drift from what ships.
 * The list is the dialog's own TEMPLATES export, so a template added there grows this shelf in
 * the same commit.
 *
 * TWO CARDS THE LIST DOES NOT CARRY:
 *
 *   Headless   real, but not a template folder — it is the `window=false` answer to the dialog's
 *              one question. No window means nothing to preview, so its thumbnail is a neutral
 *              glyph, not an iframe pretending there is a screen.
 *   Workbench  in the design, NOT here: no `_variants/workbench` exists, and a card for a
 *              template that cannot be chosen is a click that cannot work. It appears when the
 *              folder does.
 *
 * WHAT A CLICK DOES. A windowed card — the card OR its eye badge — opens the template viewer:
 * looking, full size, before committing. It never select-and-closes. The Headless card opens
 * the Blueprint with Headless already picked (there is nothing to look at), and the dashed tile
 * opens it with nothing pre-answered.
 *
 * NO AGENT COUNTS. The design captions each card "4 agents" — grouping the roster by template —
 * but `create_agent` never persists which template an agent started from, so the number does not
 * exist. The caption is absent rather than invented.
 */

import { Clock, Eye, LayoutGrid, MessageSquare, Plus } from 'lucide-react'
import type { ReactNode } from 'react'

import { TEMPLATES } from './StartModal'

const ICONS: Record<string, ReactNode> = {
  chat: <MessageSquare size={15} />,
  dashboard: <LayoutGrid size={15} />,
}

export function LaunchpadTemplateShelf({
  onDescribe,
  onHeadless,
  onPreview,
}: {
  onDescribe: () => void
  onHeadless: () => void
  onPreview: (id: string) => void
}) {
  return (
    <div className="lp-shelf">
      {TEMPLATES.map((t) => (
        <div
          key={t.id}
          className="lp-tpl"
          role="button"
          tabIndex={0}
          title={`Preview ${t.label} full size`}
          onClick={() => onPreview(t.id)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onPreview(t.id)
            }
          }}
        >
          <div className="lp-tpl-thumb">
            <iframe
              className="lp-tpl-frame"
              src={`/template-previews/${t.id}/`}
              title={`${t.label} preview`}
              tabIndex={-1}
            />
            {/* The badge and the card go the same place; it survives as the affordance that
                SAYS the card is a preview, for whoever does not try clicking the card. */}
            <button
              className="lp-tpl-eye"
              title={`Preview ${t.label} full size`}
              aria-label={`Preview ${t.label} full size`}
              onClick={(e) => {
                e.stopPropagation()
                onPreview(t.id)
              }}
            >
              <Eye size={14} />
            </button>
          </div>
          <div className="lp-tpl-caption">
            <span className="lp-tpl-name">
              {ICONS[t.id]}
              {t.label}
            </span>
            <span className="lp-tpl-blurb">{t.blurb}</span>
          </div>
        </div>
      ))}

      <div
        className="lp-tpl"
        role="button"
        tabIndex={0}
        title="Start a headless agent"
        onClick={onHeadless}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onHeadless()
          }
        }}
      >
        <div className="lp-tpl-thumb lp-tpl-thumb--plain">
          <Clock size={22} />
        </div>
        <div className="lp-tpl-caption">
          <span className="lp-tpl-name">
            <Clock size={15} />
            Headless
          </span>
          <span className="lp-tpl-blurb">A scheduled job or a voice in agentd.</span>
        </div>
      </div>

      <button className="lp-tpl-describe" onClick={onDescribe}>
        <Plus size={18} />
        Describe it instead
      </button>
    </div>
  )
}
