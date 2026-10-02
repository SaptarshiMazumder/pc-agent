/* One decision at a gate, for one shot (or the brief, or the sheet): keep it, redo it with a
 * change, or — for a still — fix one thing in it. A pick is made by clicking a still, not here.
 *
 * Nothing is sent from here. The gate collects every decision and sends them as ONE answer, so
 * the user says everything about a gate in a single message, the way the agent asked. */

import { RotateCcw, Check, Wand2 } from 'lucide-react'
import { useState } from 'react'

import type { Decision } from '../agentd/campaigns'

export function DecisionBar({
  value,
  onChange,
  fixable,
  disabled,
}: {
  value: Decision
  onChange: (d: Decision) => void
  /** The still a fix would edit; a fix is offered only when there is one. */
  fixable?: string
  disabled?: boolean
}) {
  const [draft, setDraft] = useState(value.kind === 'redo' || value.kind === 'fix' ? value.change : '')
  const mode = value.kind === 'pick' ? 'keep' : value.kind

  const set = (kind: 'keep' | 'redo' | 'fix', text = draft) => {
    if (kind === 'keep') onChange({ kind: 'keep' })
    else if (kind === 'redo') onChange({ kind: 'redo', change: text })
    else if (fixable) onChange({ kind: 'fix', still: fixable, change: text })
  }

  return (
    <div className="decision">
      <div className="decision-modes" role="radiogroup">
        <button
          className={`decision-mode${mode === 'keep' ? ' on' : ''}`}
          onClick={() => set('keep')}
          disabled={disabled}
          title="Keep it as it is"
        >
          <Check size={13} strokeWidth={2.2} /> Keep
        </button>
        <button
          className={`decision-mode${mode === 'redo' ? ' on' : ''}`}
          onClick={() => set('redo')}
          disabled={disabled}
          title="Make it again, with a change"
        >
          <RotateCcw size={13} strokeWidth={2.2} /> Redo
        </button>
        {fixable && (
          <button
            className={`decision-mode${mode === 'fix' ? ' on' : ''}`}
            onClick={() => set('fix')}
            disabled={disabled}
            title="Edit only one thing in the chosen still (Nano Banana Pro)"
          >
            <Wand2 size={13} strokeWidth={2.2} /> Fix
          </button>
        )}
      </div>
      {(mode === 'redo' || mode === 'fix') && (
        <input
          className="decision-input"
          autoFocus
          value={draft}
          disabled={disabled}
          placeholder={mode === 'fix' ? 'What to fix, only that changes — e.g. the COACH lettering' : 'What to change — e.g. softer light, she looks at the bag'}
          onChange={(e) => {
            setDraft(e.target.value)
            set(mode, e.target.value)
          }}
        />
      )}
    </div>
  )
}
