/* The right half of a stage: what it will do, what it reads, and the Run button.
 *
 *   model    the model and its facts — size, length, LoRAs — read off the stage's graph
 *   inputs   each input with the file it will be given and Change, which opens the picker
 *   prompt   folded to two lines; the whole of it on a click (changing it is the agent's — a
 *            change in the chat is `stage_set`, then Run again)
 *   Run      the person's approval for this stage. NOTHING RUNS WITHOUT IT, and it waits for what
 *            the stage needs: an input still empty, or an earlier stage with nothing made.
 */

import { ChevronDown, ChevronRight, Loader2, Play, RotateCcw } from 'lucide-react'
import { useState } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import { lengthText, picturesText, type PlanStage, type StageInputFact } from '../../agentd/stage-plan'
import { InputPicker } from './InputPicker'
import { OutputThumb } from './OutputsGrid'

const MEDIA = new Set(['image', 'video', 'audio'])
const words = (name: string): string => name.replace(/_/g, ' ')

export interface StageInput {
  inp: StageInputFact
  /** Filled by an earlier stage, not the person. */
  fed: boolean
  current: Artifact | null
  candidates: Artifact[]
  /** The file changed since the last run used it. */
  changed: boolean
}

export function StageInspector({
  stage,
  inputs,
  made,
  rendering,
  busy,
  onRun,
  onChoose,
  onUpload,
  onFromLibrary,
  onOpen,
}: {
  stage: PlanStage
  inputs: StageInput[]
  /** How many results the stage has — Run becomes Run again. */
  made: number
  rendering: boolean
  busy: boolean
  onRun: () => void
  /** A file chosen for an input: an earlier stage's result (a pick on that stage) or one of the
   *  person's files (copied into the slot). */
  onChoose: (input: StageInput, file: Artifact) => void
  onUpload: (role: string, file: File) => void
  onFromLibrary: (role: string) => void
  onOpen: (file: Artifact) => void
}) {
  const [open, setOpen] = useState('') // the input whose picker is open
  const [promptOpen, setPromptOpen] = useState(false)
  const f = stage.facts
  const facts = [picturesText(f), f.size, lengthText(f), ...f.loras.map((l) => `LoRA ${l}`)].filter(Boolean)
  const missing = inputs.filter((i) => !i.current).map((i) => words(i.inp.role))

  return (
    <aside className="stg-inspector">
      <div className="stg-insp-head">
        <span className="stg-insp-title">Run this stage</span>
        <span className="stg-insp-sub">adds a result</span>
      </div>

      <div className="stg-block">
        <span className="stg-label">Model</span>
        <div className="stg-model">
          <span className="stg-model-name">{f.model}</span>
          {facts.length > 0 && <span className="stg-model-facts">{facts.join(' · ')}</span>}
        </div>
        <span className="stg-note">Another model or size goes through the design card again.</span>
      </div>

      {inputs.length > 0 && (
        <div className="stg-block">
          <span className="stg-label">Inputs it reads</span>
          <div className="stg-inputs">
            {inputs.map((i) => (
              <div key={i.inp.role} className="stg-input">
                <div className="stg-input-row">
                  <span className="stg-input-thumb">{i.current && MEDIA.has(i.current.kind) ? <OutputThumb file={i.current} /> : null}</span>
                  <span className="stg-input-text">
                    <span className="stg-input-role">{words(i.inp.role)}</span>
                    <span className={`stg-input-from${i.current ? '' : ' is-missing'}`}>
                      {i.current ? (
                        <button type="button" className="sp-file-btn" onClick={() => onOpen(i.current as Artifact)} title="Open">
                          <span className="sp-file-name">{i.current.name}</span>
                        </button>
                      ) : i.fed ? (
                        `made by ${words(i.inp.from)} when it runs`
                      ) : (
                        'not added yet'
                      )}
                      {' · '}
                      {i.fed ? `from ${words(i.inp.from)}` : 'yours'}
                      {i.inp.frame && ` · ${i.inp.frame === 'first' ? 'opens on it' : 'ends on it'}`}
                      {i.changed && ' · changed since the last run'}
                    </span>
                  </span>
                  <button type="button" className="sp-link" disabled={busy} onClick={() => setOpen(open === i.inp.role ? '' : i.inp.role)}>
                    {open === i.inp.role ? 'Close' : 'Change'}
                  </button>
                </div>
                {open === i.inp.role && (
                  <InputPicker
                    candidates={i.candidates}
                    current={i.current}
                    fromStage={i.fed ? i.inp.from : ''}
                    disabled={busy}
                    onChoose={(a) => {
                      setOpen('')
                      onChoose(i, a)
                    }}
                    onUpload={
                      i.fed
                        ? undefined
                        : (file) => {
                            setOpen('')
                            onUpload(i.inp.role, file)
                          }
                    }
                    onFromLibrary={i.fed ? undefined : () => onFromLibrary(i.inp.role)}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {f.prompt && (
        <div className="stg-block">
          <span className="stg-label">Prompt</span>
          <button type="button" className={`sp-prompt stg-prompt${promptOpen ? ' is-open' : ''}`} onClick={() => setPromptOpen((v) => !v)}>
            {promptOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <span>{f.prompt}</span>
          </button>
          <span className="stg-note">To change it, say so in the chat — the stage is set, then run again.</span>
        </div>
      )}

      <button
        type="button"
        className="stg-run"
        disabled={busy || rendering || missing.length > 0}
        title={missing.length ? `Needs: ${missing.join(', ')}` : made ? 'Run this stage again' : 'Run this stage'}
        onClick={onRun}
      >
        {rendering ? <Loader2 size={15} className="spin" /> : made ? <RotateCcw size={15} strokeWidth={2} /> : <Play size={15} strokeWidth={2} />}
        {rendering ? 'Rendering…' : made ? 'Run again' : 'Run'}
      </button>
      {missing.length > 0 && !rendering && <span className="stg-note is-warn">Waits for: {missing.join(', ')}</span>}
      <span className="stg-note">Your click is the approval; the exact call goes to the chat.</span>
    </aside>
  )
}
