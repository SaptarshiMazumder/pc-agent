/* The design's stages as a pipeline — one card per stage with its model, where it stands and what
 * it picked, so what one stage hands the next is on screen. EVERY STAGE IS A BUTTON, always:
 * clicking it opens that stage with everything it can do. "Workflow & files" is the other view:
 * everything the creation made, across stages (outputs, the workflow pair, the folder tree). */

import { Check, FolderOpen } from 'lucide-react'

import type { Artifact } from '../../agentd/artifacts'
import { pickedResult, resultsOf, type StagePlan } from '../../agentd/stage-plan'
import { OutputThumb } from './OutputsGrid'

const words = (name: string): string => name.replace(/_/g, ' ')

/** The stage the creation is on: the first with nothing made, or '' when every stage has run. */
export function currentStage(plan: StagePlan): string {
  return plan.stages.find((s) => !resultsOf(plan.runs[s.name]).length)?.name || ''
}

function statusOf(plan: StagePlan, name: string): { text: string; tone: '' | 'is-live' | 'is-bad' | 'is-warn' } {
  const run = plan.runs[name]
  const made = resultsOf(run).length
  switch (run?.status) {
    case 'rendering':
      return { text: 'Rendering…', tone: 'is-live' }
    case 'failed':
      return { text: 'Failed — see the chat', tone: 'is-bad' }
    case 'stale':
      return { text: 'Out of date — an earlier stage changed', tone: 'is-warn' }
  }
  return made ? { text: `${made} made · 1 picked`, tone: '' } : { text: 'Not run yet', tone: '' }
}

export function StagePipeline({
  plan,
  byRel,
  shown,
  all,
  counts,
  onShow,
  onAll,
}: {
  plan: StagePlan
  byRel: (rel: string) => Artifact | null
  /** The stage on screen ('' while Workflow & files is). */
  shown: string
  all: boolean
  counts: { workflows: number; files: number }
  onShow: (stage: string) => void
  onAll: () => void
}) {
  const current = currentStage(plan)
  return (
    <nav className="pl" aria-label="Stages">
      <ol className="pl-stages">
        {plan.stages.map((s, i) => {
          const made = resultsOf(plan.runs[s.name]).length > 0
          const pick = byRel(pickedResult(plan, s.name))
          const st = statusOf(plan, s.name)
          const on = !all && s.name === shown
          return (
            <li key={s.name} className="pl-item">
              {i > 0 && (
                <span className="pl-arrow" aria-hidden="true">
                  →
                </span>
              )}
              <button
                type="button"
                className={`pl-card${on ? ' is-on' : ''}${s.name === current ? ' is-current' : ''}`}
                onClick={() => onShow(s.name)}
                aria-current={on ? 'step' : undefined}
                title={`${words(s.name)} — ${st.text}`}
              >
                <span className="pl-thumb">{pick ? <OutputThumb file={pick} /> : <span className="pl-thumb-kind">{kindOf(s.facts.model)}</span>}</span>
                <span className="pl-text">
                  <span className="pl-name">
                    <span className={`pl-mark${made ? ' is-done' : ''}`}>{made ? <Check size={10} strokeWidth={3} /> : i + 1}</span>
                    {words(s.name)}
                  </span>
                  <span className="pl-model">{s.facts.model}</span>
                  <span className={`pl-status ${st.tone}`}>{st.text}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
      <button type="button" className={`pl-all${all ? ' is-on' : ''}`} onClick={onAll} aria-pressed={all}>
        <FolderOpen size={15} strokeWidth={1.8} />
        <span className="pl-all-text">
          <span>Workflow &amp; files</span>
          <span className="pl-all-sub">
            {counts.workflows} workflow{counts.workflows === 1 ? '' : 's'} · {counts.files} file{counts.files === 1 ? '' : 's'}
          </span>
        </span>
      </button>
    </nav>
  )
}

/** A short mark for a stage with nothing made yet — read off its model's name. */
function kindOf(model: string): string {
  const m = model.toLowerCase()
  if (/wan|video|minimax|kling|seedance|animate/.test(m)) return 'VID'
  if (/audio|tts|music|voice/.test(m)) return 'AUD'
  return 'IMG'
}
