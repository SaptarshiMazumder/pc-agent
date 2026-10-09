/* The top of a creation: what it is, where it stands in the three phases, and what it reads.
 *
 *   title        the chat's name (the rail's), the newest workflow's file under it
 *   inputs chip  how many of the person's slots are filled; opens the Inputs panel
 *   phases       Design → Set up → Run (agentd/creation-phase.ts), read off the window's facts
 *   credits      the balance, beside the thing that spends it
 */

import { Check, Images } from 'lucide-react'

import type { Phase } from '../../agentd/creation-phase'
import type { Slot } from '../../agentd/reference-slots'

const PHASES: { id: Phase; label: string }[] = [
  { id: 'design', label: 'Design' },
  { id: 'setup', label: 'Set up' },
  { id: 'run', label: 'Run' },
]
const ORDER: Record<Phase, number> = { design: 0, setup: 1, run: 2 }

export function CreationHeader({
  title,
  workflowName,
  phase,
  slots,
  inputsOpen,
  onToggleInputs,
  credits,
  onCredits,
}: {
  title: string
  workflowName: string
  phase: Phase
  slots: Slot[]
  inputsOpen: boolean
  onToggleInputs: () => void
  credits: number | null
  onCredits: () => void
}) {
  /* THE COUNT IS THE PERSON'S: a slot an earlier stage fills is not theirs to add. */
  const theirs = slots.filter((s) => !s.fedBy)
  const filled = theirs.filter((s) => s.file).length
  const short = filled < theirs.length

  return (
    <header className="ch">
      <div className="ch-title">
        <span className="ch-eyebrow">Creation · Comfy Cloud</span>
        <h2 className="ch-name">{title}</h2>
        <div className="ch-chips">
          <button
            type="button"
            className={`ch-chip${inputsOpen ? ' on' : ''}${short ? ' is-short' : ''}`}
            onClick={onToggleInputs}
            title={short ? 'An input the design reads is still empty' : 'The files this creation reads'}
          >
            <Images size={13} strokeWidth={1.8} />
            {theirs.length ? `${filled} of ${theirs.length} inputs` : 'Inputs'}
            {short && <span className="ch-chip-dot" aria-hidden="true" />}
          </button>
          {workflowName && (
            <span className="ch-chip is-static st-mono" title="The newest workflow this creation emitted">
              {workflowName}
            </span>
          )}
        </div>
      </div>

      <div className="ch-side">
        <div className="ch-phases" aria-label="Phase">
          {PHASES.map((p, i) => {
            const done = ORDER[p.id] < ORDER[phase]
            const now = p.id === phase
            return (
              <span key={p.id} className="ch-phase-wrap">
                {i > 0 && (
                  <span className="ch-phase-arrow" aria-hidden="true">
                    →
                  </span>
                )}
                <span className={`ch-phase${done ? ' is-done' : ''}${now ? ' is-now' : ''}`} aria-current={now ? 'step' : undefined}>
                  {done ? <Check size={11} strokeWidth={3} /> : `${i + 1} ·`} {p.label}
                </span>
              </span>
            )
          })}
        </div>
        <button type="button" className="ch-credits st-mono" onClick={onCredits} title="Credits">
          {credits != null ? `${credits.toLocaleString()} cr` : '—'}
        </button>
      </div>
    </header>
  )
}
