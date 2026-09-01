/* PLACEHOLDER WIDGET — the thing the agent MADE. See ./README.md.
 *
 * @placeholder — SCAFFOLDING, not a decision. It shows the look, the shape and the wiring; it is
 * not what this agent is for. Adopt it (change it, rename the file, delete this tag) or delete
 * the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * NOT WIRED TO ANYTHING, on purpose. A result belongs in the transcript beside the turn that
 * produced it, which means rendering it from a tool's own result or from an artifact — see
 * `agentd/artifacts.ts` and `components/ArtifactView.tsx`. Dropping it at a fixed spot in the
 * layout would put yesterday's answer above today's question.
 *
 * WHAT IT IS FOR: an answer that is a THING rather than a paragraph — a recipe, a query, a
 * config, a plan. Prose says "you're missing cream"; this shows the whole list with cream marked,
 * which is the difference between reading an answer and being able to act on it.
 *
 * THE MISSING ONES ARE THE POINT. A card that lists only what you have is a card you have to
 * diff in your head. `chips` carries an explicit `missing` flag so the gap is on screen, in the
 * one colour that means "this needs you".
 */

import './widgets.css'

import { ArrowUpRight } from 'lucide-react'

export interface ResultChip {
  label: string
  /** Draws it in the danger tone with a hairline — "you do not have this". */
  missing?: boolean
}

export interface ResultCardData {
  title: string
  /** The one line under the title: whatever three facts make this result judgeable at a glance. */
  meta?: string
  chips?: ResultChip[]
}

export const SAMPLE_RESULT: ResultCardData = {
  title: 'What the agent produced',
  meta: 'a placeholder · three facts about it · replace this',
  chips: [
    { label: 'something you have' },
    { label: 'and another' },
    { label: 'one you do not', missing: true },
  ],
}

export default function PlaceholderResultCard({
  data = SAMPLE_RESULT,
  onOpen,
}: {
  data?: ResultCardData
  /** Optional. The arrow is only drawn when there is somewhere to go — a button that does
   *  nothing is worse than no button. */
  onOpen?: () => void
}) {
  return (
    <div className="result-card">
      <div className="result-card-head">
        <div className="result-card-text">
          <span className="result-card-title">{data.title}</span>
          {data.meta && <span className="result-card-meta">{data.meta}</span>}
        </div>
        {onOpen && (
          <button className="result-card-open" onClick={onOpen} title="Open" aria-label="Open">
            <ArrowUpRight size={15} strokeWidth={1.8} />
          </button>
        )}
      </div>
      {data.chips && data.chips.length > 0 && (
        <div className="result-chips">
          {data.chips.map((c) => (
            <span key={c.label} className={`result-chip${c.missing ? ' is-missing' : ''}`}>
              {c.missing ? `${c.label} — missing` : c.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
