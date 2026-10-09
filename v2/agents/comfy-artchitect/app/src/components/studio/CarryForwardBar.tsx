/* The foot of a stage: what it hands on — its pick — and the way to the next stage. Running is
 * opening each stage and pressing Run; this makes the next one a click away. On the last stage it
 * says so. The pick itself is changed in the results (a click on any of them). */

import { ArrowRight } from 'lucide-react'

import type { Artifact } from '../../agentd/artifacts'
import { pickedResult, resultsOf, type StagePlan } from '../../agentd/stage-plan'
import { OutputThumb } from './OutputsGrid'

const words = (name: string): string => name.replace(/_/g, ' ')

export function CarryForwardBar({
  plan,
  index,
  byRel,
  onShow,
}: {
  plan: StagePlan
  index: number
  byRel: (rel: string) => Artifact | null
  onShow: (stage: string) => void
}) {
  const stage = plan.stages[index]
  const next = plan.stages[index + 1]
  const made = resultsOf(plan.runs[stage.name]).length
  const pick = byRel(pickedResult(plan, stage.name))

  return (
    <div className="cf">
      {pick && (
        <span className="cf-thumb">
          <OutputThumb file={pick} />
        </span>
      )}
      <div className="cf-text">
        <span className="cf-main">{pick ? `${pick.name} is picked` : made ? 'Nothing picked yet' : 'Nothing made yet'}</span>
        <span className="cf-sub">
          {pick
            ? next
              ? `${words(next.name)} reads it. Change the pick any time.`
              : 'It is the result of this creation. Change the pick any time.'
            : made
              ? 'Click a result to pick it.'
              : 'Press Run on the right.'}
        </span>
      </div>
      {next ? (
        <button type="button" className="cf-go" onClick={() => onShow(next.name)}>
          Continue to {words(next.name)} <ArrowRight size={15} strokeWidth={2.2} />
        </button>
      ) : (
        <span className="cf-last">Last stage</span>
      )}
    </div>
  )
}
