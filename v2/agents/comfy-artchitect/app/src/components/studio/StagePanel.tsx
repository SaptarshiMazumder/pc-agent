/* One stage of the pipeline: what it made on the left (StageResults), what it will do and the Run
 * on the right (StageInspector).
 *
 * THIS FILE IS THE STAGE'S CONTROLLER: it works out each input's file and candidates from the plan
 * and the chat's files, and hands the halves what they show. Picking and setting inputs are
 * instant and free; only Run starts work, and only with the person's click.
 */

import type { Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'
import { pickedResult, resultsOf, type PlanStage, type StagePlan } from '../../agentd/stage-plan'
import { StageInspector, type StageInput } from './StageInspector'
import { StageResults } from './StageResults'

const KIND: Record<string, string> = { IMAGE: 'image', VIDEO: 'video', AUDIO: 'audio' }
const MEDIA = new Set(['image', 'video', 'audio'])

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
  /** Pick one of `stage`'s results — what the stages after it are given. */
  onPick: (stage: string, file: Artifact) => void
  onSetInput: (role: string, file: Artifact, current: Artifact | null) => void
  onUpload: (role: string, file: File) => void
  onFromLibrary: (role: string) => void
  onOpen: (a: Artifact) => void
}) {
  const run = plan.runs[stage.name]
  const made = resultsOf(run).map(byRel).filter((a): a is Artifact => !!a)
  const picked = pickedResult(plan, stage.name)

  const inputs: StageInput[] = stage.facts.inputs.map((inp) => {
    const fed = inp.from !== 'you'
    const current = fed ? byRel(pickedResult(plan, inp.from)) : slots.find((s) => s.role === inp.role)?.file || null
    const candidates = fed
      ? resultsOf(plan.runs[inp.from]).map(byRel).filter((a): a is Artifact => !!a)
      : chatMedia.filter((a) => (KIND[inp.type] ? a.kind === KIND[inp.type] : MEDIA.has(a.kind)))
    const usedRel = run?.fed?.[inp.role] || ''
    const changed = !!usedRel && !!current && !current.path.replace(/\\/g, '/').endsWith(usedRel)
    return { inp, fed, current, candidates, changed }
  })
  const status = run?.status || ''
  const rendering = status === 'rendering'
  const hint = rendering
    ? 'rendering…'
    : status === 'failed'
      ? 'failed — see the chat'
      : status === 'stale'
        ? 'out of date — an earlier stage changed'
        : made.length
          ? `${made.length} made · run it again any time`
          : 'not run yet'

  return (
    <div className={`stg${status === 'failed' ? ' is-failed' : ''}`}>
      <StageResults
        index={index}
        stage={stage}
        made={made}
        picked={picked}
        hint={hint}
        rendering={rendering}
        failedWhy={status === 'failed' ? run?.why || '' : ''}
        busy={busy}
        onPick={(file) => onPick(stage.name, file)}
        onOpen={onOpen}
      />
      <StageInspector
        stage={stage}
        inputs={inputs}
        made={made.length}
        rendering={rendering}
        busy={busy}
        onRun={() => onRun(stage.name)}
        onChoose={(i, file) => (i.fed ? onPick(i.inp.from, file) : onSetInput(i.inp.role, file, i.current))}
        onUpload={onUpload}
        onFromLibrary={onFromLibrary}
        onOpen={onOpen}
      />
    </div>
  )
}
