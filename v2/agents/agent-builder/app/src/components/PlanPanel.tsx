/* The plan, pinned above the composer.
 *
 * A PROGRESS INDICATOR, NOT A DOCUMENT. Collapsed it is one line — the counter and the step being
 * worked on — because that answers "where is it up to" without costing any of the conversation's
 * room. Expanded it is the checklist.
 *
 * It sits here rather than in the thread because it is the CURRENT state of the work. `update_plan`
 * sends the whole list every time it is called, so a plan in the transcript means the fourth
 * re-plan leaves three stale copies of a growing list scrolled up the page, each looking equally
 * authoritative.
 */

import { useEffect, useState } from 'react'
import type { Plan, PlanStep } from '../agentd/chat'

export function PlanPanel({ plan }: { plan: Plan }) {
  const [open, setOpen] = useState(false)

  const done = plan.steps.filter((s) => s.status === 'completed').length
  const current = plan.steps.find((s) => s.status === 'in_progress')
  // Every step finished and nothing running: say so rather than showing the last step as if it
  // were still going.
  const headline = current?.step || (done === plan.steps.length ? 'All steps complete' : plan.explanation || 'Planning')

  // A re-plan while collapsed should not silently change what the one visible line says without
  // the user noticing the count move — so a new plan re-collapses to show the fresh summary.
  useEffect(() => setOpen(false), [plan])

  return (
    <div className={`plan ${open ? 'open' : ''}`}>
      <button className="plan-head" onClick={() => setOpen((v) => !v)}>
        <span className="plan-count">
          {done}/{plan.steps.length}
        </span>
        <span className="plan-now">{headline}</span>
        <span className="plan-caret">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <ul className="plan-steps">
          {plan.steps.map((s, i) => (
            <li key={`${i}-${s.step}`} className={`plan-step ${s.status}`}>
              <span className="plan-box">{mark(s)}</span>
              <span className="plan-text">{s.step}</span>
              {s.tool && <span className="plan-tool">{s.tool}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function mark(step: PlanStep): string {
  if (step.status === 'completed') return '✓'
  if (step.status === 'in_progress') return '●'
  return ''
}
