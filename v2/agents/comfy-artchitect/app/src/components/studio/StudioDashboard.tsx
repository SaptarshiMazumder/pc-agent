/* The studio: the creation as a pipeline of stages, beside the conversation that drives it.
 *
 *   CreationHeader    what it is, where it stands (Design → Set up → Run), its inputs, the balance
 *   StagePipeline     one card per stage of the design, and "Workflow & files"
 *   the body          Design: the agent's card (AskPanel) and the inputs it declared
 *                     Set up / Run: the stage on screen — results | inspector — and its carry bar
 *                     Workflow & files: outputs, the workflow pair, the folder tree
 *   FileViewer        beside the body whenever a file is picked — from a tile, a slot, the tree
 *                     or a thumbnail in the conversation
 *
 * A chat with nothing said yet shows the new-creation setup instead (NewCreationSetup).
 *
 * ONE VIEWER, as before: nothing opens by itself (the thumbnail rule, agentd/artifacts.ts); the
 * original bytes load only on a click.
 */

import { useEffect, useMemo, useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import type { LibrarySaveOutcome } from '../../agentd/library'

import type { Artifact } from '../../agentd/artifacts'
import type { ThreadItem } from '../../agentd/chat'
import { pendingAsk, phaseOf } from '../../agentd/creation-phase'
import type { LibraryItem } from '../../agentd/library'
import type { Slot } from '../../agentd/reference-slots'
import { useStagePlan } from '../../agentd/stage-plan'
import { useApp, type LibraryTab } from '../../state/store'
import { collectWorkflows } from '../workflows/WorkflowCard'
import { ActiveRunStrip } from './ActiveRunStrip'
import { CreationHeader } from './CreationHeader'
import { DesignPhase } from './DesignPhase'
import { FileViewer } from './FileViewer'
import { NewCreationSetup } from './NewCreationSetup'
import { ReferenceSlots } from './ReferenceSlots'
import { StagePipeline, currentStage } from './StagePipeline'
import { StagePlan, useByRel } from './StagePlan'
import { installing, type StudioState } from './useStudioState'
import { WorkflowFilesView } from './WorkflowFilesView'

import './studio.css'

const MEDIA = new Set(['image', 'video', 'audio'])
const inReferences = (a: Artifact): boolean => /[\\/]references[\\/]/.test(a.path)

export function StudioDashboard({
  client,
  state,
  running,
  items,
  empty,
  title,
  workflowName,
  artifacts,
  slots,
  freeReferences,
  onAddReference,
  referencesDisabled,
  credits,
  onCredits,
  onDeleteFiles,
  onAddToLibrary,
  deletionDisabled,
  sessionKey,
  workspaceVersion,
  onRunAgain,
  onOpenLibrary,
  onFromLibrary,
  onSaveTemplate,
  onSend,
  onDecide,
}: {
  client: AgentdClient | undefined
  /** The one studio-state poll (App owns it; the install panel reads it too). */
  state: StudioState
  running: boolean
  /** The conversation — the design card is read off it (agentd/creation-phase.ts). */
  items: ThreadItem[]
  /** Nothing said yet: the new-creation setup instead of a creation. */
  empty: boolean
  /** The chat's name, from the rail. */
  title: string
  /** The newest workflow this creation emitted. */
  workflowName: string
  /** Everything the agent wrote this session — the creation's whole content. */
  artifacts: Artifact[]
  /** The reference slots the agent asked for, with the file filling each (agentd/reference-slots). */
  slots: Slot[]
  /** Reference files in this chat's folder that fill no slot. */
  freeReferences: Artifact[]
  onAddReference: (file: File, role: string | null) => Promise<void>
  referencesDisabled: boolean
  credits: number | null
  onCredits: () => void
  /** Delete: the daemon removes the files, after the one warning each section shows. */
  onDeleteFiles?: (paths: string[]) => Promise<void>
  /** Add to Library: copies into the shared Library; answers a sentence to show. */
  onAddToLibrary?: (paths: string[]) => Promise<LibrarySaveOutcome>
  deletionDisabled?: string
  sessionKey: string
  /** Bumped whenever the workspace changes, so the stage records are re-read. */
  workspaceVersion: number
  /** Start a new conversation around a Library workflow (the kept workflows on a new creation). */
  onRunAgain: (item: LibraryItem) => void
  /** Open the Library page on a tab. */
  onOpenLibrary: (tab: LibraryTab) => void
  /** A slot's From Library door: the Library page on Inputs, with that role waiting. */
  onFromLibrary: (role: string) => void
  /** Keep every workflow of this chat as one template (asks for its name first). */
  onSaveTemplate?: () => void
  /** Send a message to this chat — a stage's Run sends the call carrying its approval. */
  onSend: (text: string) => void
  /** Answer the design card (AskPanel) — the same send the thread's card uses. */
  onDecide: (reply: string) => void
}) {
  const selectedPath = useApp((s) => s.selectedArtifactPath)
  const setSelectedPath = useApp((s) => s.selectArtifact)

  const made = useMemo(() => artifacts.filter((a) => !inReferences(a)), [artifacts])
  const outputs = useMemo(() => made.filter((a) => MEDIA.has(a.kind)), [made])
  const workflowSide = useMemo(() => made.filter((a) => !MEDIA.has(a.kind)), [made])
  const workflowCount = useMemo(
    () => collectWorkflows(workflowSide.filter((a) => !/^install_/i.test(a.name))).length,
    [workflowSide],
  )

  // SELECT BY PATH, RESOLVE BY LOOKUP: the same file is re-declared as later turns touch it, and
  // holding the object would pin whichever copy was clicked.
  const selected = useMemo(() => artifacts.find((a) => a.path === selectedPath) || null, [artifacts, selectedPath])

  // A selection from another chat does not apply here.
  useEffect(() => {
    if (selectedPath && !artifacts.some((a) => a.path === selectedPath)) setSelectedPath('')
  }, [artifacts, selectedPath, setSelectedPath])

  /* THE STAGES of this chat's design, once there is one (agentd/stage-plan.ts). */
  const plan = useStagePlan(client, sessionKey, workspaceVersion)
  const byRel = useByRel(artifacts, sessionKey)
  const ask = useMemo(() => pendingAsk(items), [items])
  const phase = phaseOf(items, plan, installing(state))

  /* WHICH STAGE IS ON SCREEN. The current one (the first with nothing made), following the
     creation as it moves — unless the person opened another. "Workflow & files" is the other view. */
  const current = plan ? currentStage(plan) : ''
  const [shown, setShown] = useState('')
  const [all, setAll] = useState(false)
  const [inputsOpen, setInputsOpen] = useState(false)
  useEffect(() => {
    setShown('')
    setAll(false)
    setInputsOpen(false)
  }, [sessionKey])
  useEffect(() => setShown(''), [current])
  const show = (name: string): void => {
    setShown(name)
    setAll(false)
  }
  const stageShown = plan ? (plan.stages.some((s) => s.name === shown) ? shown : current || plan.stages[plan.stages.length - 1].name) : ''

  /* THE AGENT ASKED FOR A PHOTO: the inputs panel opens, once, when an empty slot appears mid-
     creation. A saved chat's slots arriving with its history are its opening state, not an ask. */
  const emptySlots = slots.filter((s) => !s.file && !s.fedBy).length
  const [seenEmpty, setSeenEmpty] = useState(emptySlots)
  useEffect(() => {
    if (emptySlots > seenEmpty && phase !== 'design') setInputsOpen(true)
    setSeenEmpty(emptySlots)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emptySlots])

  const open = (a: Artifact): void => setSelectedPath(a.path)

  if (empty)
    return (
      <div className="st-dash">
        <div className="st-body">
          <div className={`ws${selected ? ' is-narrow' : ''}`}>
            <NewCreationSetup
              client={client}
              workspaceVersion={workspaceVersion}
              slots={slots}
              free={freeReferences}
              referencesDisabled={referencesDisabled}
              onAddReference={onAddReference}
              onOpen={open}
              onFromLibrary={onFromLibrary}
              onOpenLibrary={onOpenLibrary}
              onRunAgain={onRunAgain}
            />
          </div>
          {selected && (
            <main className="st-view">
              <FileViewer key={selected.path} file={selected} onClose={() => setSelectedPath('')} />
            </main>
          )}
        </div>
      </div>
    )

  return (
    <div className="st-dash">
      <ActiveRunStrip state={state} client={client} />

      <div className="st-body">
        <div className={`ws${selected ? ' is-narrow' : ''}`}>
          <CreationHeader
            title={title}
            workflowName={workflowName}
            phase={phase}
            slots={slots}
            inputsOpen={inputsOpen}
            onToggleInputs={() => setInputsOpen((v) => !v)}
            credits={credits}
            onCredits={onCredits}
          />
          {inputsOpen && phase !== 'design' && (
            <section className="ch-inputs">
              <ReferenceSlots
                slots={slots}
                free={freeReferences}
                disabled={referencesDisabled}
                onAdd={onAddReference}
                onOpen={open}
                onFromLibrary={onFromLibrary}
              />
            </section>
          )}

          {plan && (
            <StagePipeline
              plan={plan}
              byRel={byRel}
              shown={stageShown}
              all={all}
              counts={{ workflows: workflowCount, files: made.length }}
              onShow={show}
              onAll={() => setAll(true)}
            />
          )}

          {all ? (
            <WorkflowFilesView
              outputs={outputs}
              workflowSide={workflowSide}
              made={made}
              workflowCount={workflowCount}
              selected={selected}
              selectedPath={selectedPath}
              onOpen={open}
              onDeleteFiles={onDeleteFiles}
              onAddToLibrary={onAddToLibrary}
              onSaveTemplate={onSaveTemplate}
              deletionDisabled={deletionDisabled}
            />
          ) : phase === 'design' ? (
            <DesignPhase
              ask={ask}
              onDecide={onDecide}
              hasPlan={!!plan}
              running={running}
              slots={slots}
              free={freeReferences}
              referencesDisabled={referencesDisabled}
              onAddReference={onAddReference}
              onOpen={open}
              onFromLibrary={onFromLibrary}
            />
          ) : (
            plan && (
              <StagePlan
                client={client}
                sessionKey={sessionKey}
                plan={plan}
                shown={stageShown}
                files={artifacts}
                slots={slots}
                running={running}
                onAddReference={onAddReference}
                onFromLibrary={onFromLibrary}
                onOpen={open}
                onShow={show}
                onSend={onSend}
              />
            )
          )}
        </div>

        {/* THE PANE IS NOT RENDERED WHEN NOTHING IS SELECTED — the body takes the room. KEYED BY
            PATH, so picking a different file MOUNTS A NEW VIEWER instead of handing the old one
            new props — an <img> otherwise goes on painting the previous render until the new
            bytes land. */}
        {selected && (
          <main className="st-view">
            <FileViewer key={selected.path} file={selected} onClose={() => setSelectedPath('')} />
          </main>
        )}
      </div>
    </div>
  )
}
