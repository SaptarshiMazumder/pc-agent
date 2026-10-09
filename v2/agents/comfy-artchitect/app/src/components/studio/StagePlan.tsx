/* The stage on screen — one stage of this chat's design (StagePanel) with the carry-forward bar
 * under it. The pipeline above it (StagePipeline) chooses which; this hosts it.
 *
 * WHAT A CLICK DOES (agentd/stage-plan.ts): a pick rewrites stage_picks.json; an input chosen for
 * a slot is copied into the chat's references folder under the role; Run records a one-time
 * approval (stage_run_approve) and sends the chat the exact pipeline_run call carrying it. Picking
 * and setting inputs are instant and free; only Run starts work. */

import type { AgentdClient } from '@agentd/client'
import { useMemo, useState } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'
import { approveStage, runMessage, savePick, setInput, type StagePlan as Plan } from '../../agentd/stage-plan'
import { relOfChatFile } from '../../agentd/workspace-files'
import { useApp } from '../../state/store'
import { CarryForwardBar } from './CarryForwardBar'
import { StagePanel } from './StagePanel'

import './stage-plan.css'

const MEDIA = new Set(['image', 'video', 'audio'])

/** This chat's files by workspace-relative path — the lookup every stage view reads with. */
export function useByRel(files: Artifact[], sessionKey: string): (rel: string) => Artifact | null {
  const map = useMemo(() => {
    const m = new Map<string, Artifact>()
    for (const a of files) {
      const rel = relOfChatFile(a.path, sessionKey)
      if (rel) m.set(rel, a)
    }
    return m
  }, [files, sessionKey])
  return (rel: string) => (rel ? map.get(rel) || null : null)
}

export function StagePlan({
  client,
  sessionKey,
  plan,
  shown,
  files,
  slots,
  running,
  onAddReference,
  onFromLibrary,
  onOpen,
  onShow,
  onSend,
}: {
  client: AgentdClient | undefined
  sessionKey: string
  plan: Plan
  /** The stage on screen, by name. */
  shown: string
  /** This chat's files (all three folders). */
  files: Artifact[]
  slots: Slot[]
  /** A turn is going — a Run now would land in the middle of it. */
  running: boolean
  onAddReference: (file: File, role: string | null) => Promise<void>
  onFromLibrary: (role: string) => void
  onOpen: (a: Artifact) => void
  onShow: (stage: string) => void
  /** Send a message to this chat. */
  onSend: (text: string) => void
}) {
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const bump = useApp((s) => s.bumpWorkspace)
  const byRel = useByRel(files, sessionKey)
  const chatMedia = useMemo(() => files.filter((a) => MEDIA.has(a.kind)), [files])

  const index = Math.max(
    0,
    plan.stages.findIndex((s) => s.name === shown),
  )
  const stage = plan.stages[index]

  /* EVERY CLICK SAYS WHAT WENT WRONG. A pick or a copy the daemon refused leaves the panel as it
     was, with the reason under it — never a silent no-op. */
  const act = async (what: () => Promise<void>): Promise<void> => {
    if (!client) return
    setWorking(true)
    setError('')
    try {
      await what()
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setWorking(false)
    }
  }

  const relOf = (a: Artifact): string => {
    const rel = relOfChatFile(a.path, sessionKey)
    if (!rel) throw new Error(`${a.name} is not a file of this chat`)
    return rel
  }

  if (!stage) return null
  return (
    <div className="stg-host">
      <StagePanel
        key={stage.name}
        index={index}
        stage={stage}
        plan={plan}
        byRel={byRel}
        chatMedia={chatMedia}
        slots={slots}
        busy={!client || running || working}
        onRun={(name) =>
          void act(async () => {
            const token = await approveStage(client!, sessionKey, name)
            onSend(runMessage(name, token))
          })
        }
        onPick={(name, file) => void act(() => savePick(client!, sessionKey, plan, name, relOf(file)))}
        onSetInput={(role, file, current) => void act(() => setInput(client!, sessionKey, role, file, current))}
        onUpload={(role, file) => void act(() => onAddReference(file, role))}
        onFromLibrary={onFromLibrary}
        onOpen={onOpen}
      />
      {error && <div className="sp-error">{error}</div>}
      <CarryForwardBar plan={plan} index={index} byRel={byRel} onShow={onShow} />
    </div>
  )
}
