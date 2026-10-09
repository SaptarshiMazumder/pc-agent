/* The Stages section of the Workspace — the pipeline as steps the person drives, as in Ad Studio.
 *
 * Each step (StagePanel) shows what it reads, what it will do and what it made. The person sets
 * every input (their files, any earlier result), picks which result goes on, and presses Run on
 * each step: the panel records the one-time approval and sends the chat the exact call carrying
 * it (agentd/stage-plan.ts). Picking and setting inputs are instant and free; only Run starts work.
 *
 * Shown once the agent has a design (stage_plan.json exists); before that there are no steps.
 */

import type { AgentdClient } from '@agentd/client'
import { useMemo, useState } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'
import { approveStage, runMessage, savePick, setInput, type StagePlan as Plan } from '../../agentd/stage-plan'
import { relOfChatFile } from '../../agentd/workspace-files'
import { useApp } from '../../state/store'
import { StagePanel } from './StagePanel'

import './stage-plan.css'

const MEDIA = new Set(['image', 'video', 'audio'])

export function StagePlan({
  client,
  sessionKey,
  plan,
  files,
  slots,
  running,
  onAddReference,
  onFromLibrary,
  onOpen,
  onSend,
}: {
  client: AgentdClient | undefined
  sessionKey: string
  plan: Plan
  /** This chat's files (all three folders). */
  files: Artifact[]
  slots: Slot[]
  /** A turn is going — a Run now would land in the middle of it. */
  running: boolean
  onAddReference: (file: File, role: string | null) => Promise<void>
  onFromLibrary: (role: string) => void
  onOpen: (a: Artifact) => void
  /** Send a message to this chat. */
  onSend: (text: string) => void
}) {
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const bump = useApp((s) => s.bumpWorkspace)

  const byRelMap = useMemo(() => {
    const m = new Map<string, Artifact>()
    for (const a of files) {
      const rel = relOfChatFile(a.path, sessionKey)
      if (rel) m.set(rel, a)
    }
    return m
  }, [files, sessionKey])
  const byRel = (rel: string): Artifact | null => (rel ? byRelMap.get(rel) || null : null)
  const chatMedia = useMemo(() => files.filter((a) => MEDIA.has(a.kind)), [files])

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

  return (
    <div className="sp-list">
      {plan.stages.map((stage, i) => (
        <StagePanel
          key={stage.name}
          index={i}
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
      ))}
      {error && <div className="sp-error">{error}</div>}
    </div>
  )
}
