/* One stage of the pipeline: what it reads, what it will do, what it made — and its Run button.
 *
 *   head     step number, name, model, where it stands; Run (the person's approval for this step)
 *   facts    size · length · LoRAs · "you see it first" — read off the stage's graph, never prose
 *   inputs   each input with the file it will be given and Change, which opens the picker
 *   results  everything it has made; click one to pick what the next step is given
 *   prompt   folded to two lines; the whole of it on a click
 *
 * NOTHING RUNS WITHOUT THE BUTTON, and the button waits for what the step needs: an input of the
 * person's still empty, or an earlier step with nothing made, says so instead of running.
 */

import { ChevronDown, ChevronRight, Eye, Loader2, Play, RotateCcw } from 'lucide-react'
import { useState } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'
import { lengthText, pickedResult, picturesText, resultsOf, type PlanStage, type StagePlan } from '../../agentd/stage-plan'
import { InputPicker } from './InputPicker'
import { OutputThumb } from './OutputsGrid'

const KIND: Record<string, string> = { IMAGE: 'image', VIDEO: 'video', AUDIO: 'audio' }
const MEDIA = new Set(['image', 'video', 'audio'])
const words = (name: string): string => name.replace(/_/g, ' ')

export function StagePanel({
  index,
  stage,
  plan,
  byRel,
  chatMedia,
  slots,
  busy,
  onRun,
  onPick,
  onSetInput,
  onUpload,
  onFromLibrary,
  onOpen,
}: {
  index: number
  stage: PlanStage
  plan: StagePlan
  /** This chat's files by workspace-relative path. */
  byRel: (rel: string) => Artifact | null
  /** Every media file of this chat — what an input the person fills can be given. */
  chatMedia: Artifact[]
  slots: Slot[]
  /** A turn is going, the daemon is away, or a click is in flight: nothing new starts. */
  busy: boolean
  onRun: (stage: string) => void
  /** Pick one of `stage`'s results — what the steps after it are given. */
  onPick: (stage: string, file: Artifact) => void
  onSetInput: (role: string, file: Artifact, current: Artifact | null) => void
  onUpload: (role: string, file: File) => void
  onFromLibrary: (role: string) => void
  onOpen: (a: Artifact) => void
}) {
  const [open, setOpen] = useState('') // the input whose picker is open
  const [promptOpen, setPromptOpen] = useState(false)
  const f = stage.facts
  const run = plan.runs[stage.name]
  const made = resultsOf(run).map(byRel).filter((a): a is Artifact => !!a)
  const picked = pickedResult(plan, stage.name)

  const inputs = f.inputs.map((inp) => {
    const fed = inp.from !== 'you'
    const current = fed
      ? byRel(pickedResult(plan, inp.from))
      : slots.find((s) => s.role === inp.role)?.file || null
    const candidates = fed
      ? resultsOf(plan.runs[inp.from]).map(byRel).filter((a): a is Artifact => !!a)
      : chatMedia.filter((a) => (KIND[inp.type] ? a.kind === KIND[inp.type] : MEDIA.has(a.kind)))
    const usedRel = run?.fed?.[inp.role] || ''
    const changed = !!usedRel && !!current && !current.path.replace(/\\/g, '/').endsWith(usedRel)
    return { inp, fed, current, candidates, changed }
  })
  const missing = inputs.filter((i) => !i.current).map((i) => words(i.inp.role))
  const status = run?.status || ''
  const rendering = status === 'rendering'
  const hint =
    rendering ? 'rendering…'
    : status === 'failed' ? 'failed — see the chat'
    : status === 'stale' ? 'out of date — an earlier step changed'
    : made.length ? `${made.length} made`
    : 'not run yet'
  const facts = [picturesText(f), f.size, lengthText(f), ...f.loras.map((l) => `LoRA ${l}`)].filter(Boolean)

  return (
    <div className={`sp${status === 'failed' ? ' is-failed' : ''}`}>
      <div className="sp-head">
        <span className="sp-num">{index + 1}</span>
        <span className="sp-name">{words(stage.name)}</span>
        <span className="sp-model">{f.model}</span>
        <span className="sp-hint">{hint}</span>
        <span className="sp-grow" />
        <button
          type="button"
          className="sp-run"
          disabled={busy || rendering || missing.length > 0}
          title={missing.length ? `Needs: ${missing.join(', ')}` : made.length ? 'Run this step again' : 'Run this step'}
          onClick={() => onRun(stage.name)}
        >
          {rendering ? <Loader2 size={13} className="spin" /> : made.length ? <RotateCcw size={13} /> : <Play size={13} />}
          {made.length ? 'Run again' : 'Run'}
        </button>
      </div>

      {(facts.length > 0 || f.review) && (
        <ul className="sp-facts">
          {facts.map((x) => (
            <li key={x}>{x}</li>
          ))}
          {f.review && <li>you see it before the next step</li>}
        </ul>
      )}

      {inputs.length > 0 && (
        <div className="sp-inputs">
          {inputs.map(({ inp, fed, current, candidates, changed }) => (
            <div key={inp.role} className="sp-input">
              <div className="sp-input-row">
                <span className="sp-role">{words(inp.role)}</span>
                <span className={`sp-file${current ? '' : ' is-missing'}`}>
                  {current ? (
                    <button type="button" className="sp-file-btn" onClick={() => onOpen(current)} title="Open">
                      {MEDIA.has(current.kind) && (
                        <span className="sp-file-thumb">
                          <OutputThumb file={current} />
                        </span>
                      )}
                      <span className="sp-file-name">{current.name}</span>
                    </button>
                  ) : fed ? (
                    `made by ${words(inp.from)} when it runs`
                  ) : (
                    'not added yet'
                  )}
                </span>
                <span className="sp-src">
                  {fed ? `from ${words(inp.from)}` : 'yours'}
                  {inp.frame && ` · ${inp.frame === 'first' ? 'opens on it' : 'ends on it'}`}
                  {changed && ' · changed since the last run'}
                </span>
                <button
                  type="button"
                  className="sp-link"
                  disabled={busy}
                  onClick={() => setOpen(open === inp.role ? '' : inp.role)}
                >
                  {open === inp.role ? 'Close' : 'Change'}
                </button>
              </div>
              {open === inp.role && (
                <InputPicker
                  candidates={candidates}
                  current={current}
                  fromStage={fed ? inp.from : ''}
                  disabled={busy}
                  onChoose={(a) => {
                    setOpen('')
                    if (fed) onPick(inp.from, a)
                    else onSetInput(inp.role, a, current)
                  }}
                  onUpload={fed ? undefined : (file) => {
                    setOpen('')
                    onUpload(inp.role, file)
                  }}
                  onFromLibrary={fed ? undefined : () => onFromLibrary(inp.role)}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {made.length > 0 && (
        <div className="sp-results">
          {made.map((a) => {
            const isPicked = !!picked && a.path.replace(/\\/g, '/').endsWith(picked)
            return (
              <div key={a.path} className={`sp-tile${isPicked ? ' is-picked' : ''}`}>
                <button
                  type="button"
                  className="sp-tile-pick"
                  disabled={busy}
                  title={isPicked ? 'Picked — this is what the next step gets' : 'Pick this one'}
                  onClick={() => onPick(stage.name, a)}
                >
                  <OutputThumb file={a} />
                </button>
                <button type="button" className="sp-tile-open" title="Open" onClick={() => onOpen(a)}>
                  <Eye size={12} />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {f.prompt && (
        <button type="button" className={`sp-prompt${promptOpen ? ' is-open' : ''}`} onClick={() => setPromptOpen((v) => !v)}>
          {promptOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <span>{f.prompt}</span>
        </button>
      )}
    </div>
  )
}
