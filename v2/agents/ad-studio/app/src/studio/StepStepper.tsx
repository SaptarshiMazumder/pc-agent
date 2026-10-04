/* The campaign's checklist as a row of steps — the recipe's order plus any step the user added.
 *
 * EVERY STEP IS A BUTTON, always: done, skipped or not started, clicking it opens that step with
 * everything it can do. The current step (the first one not done) is only highlighted. */

import { Check, Minus } from 'lucide-react'

import type { CampaignStep } from '../agentd/campaigns'

export function StepStepper({
  steps,
  current,
  shown,
  onShow,
}: {
  steps: CampaignStep[]
  current: string
  shown: string
  onShow: (step: string) => void
}) {
  return (
    <ol className="stepper">
      {steps.map((s, i) => (
        <li
          key={s.id}
          className={`step${s.status === 'done' ? ' past' : ''}${s.id === current ? ' now' : ''}${s.id === shown ? ' shown' : ''}${s.status === 'skipped' ? ' skipped' : ''}`}
        >
          <button className="step-btn" onClick={() => onShow(s.id)} title={`${s.title} — ${s.status}${s.results.length ? `, ${s.results.length} made` : ''}`}>
            <span className="step-num">
              {s.status === 'done' ? <Check size={11} strokeWidth={3} /> : s.status === 'skipped' ? <Minus size={11} /> : i + 1}
            </span>
            <span className="step-label">{s.title}</span>
            {s.results.length > 0 && <span className="step-count">{s.results.length}</span>}
          </button>
        </li>
      ))}
    </ol>
  )
}
