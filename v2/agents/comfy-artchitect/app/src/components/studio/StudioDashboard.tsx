/* The stage: what the agent made, beside the conversation that made it.
 *
 * TWO TABS, TWO SCOPES (creative-studio redesign, Sep 2026):
 *
 *   Workspace   THIS chat's files, on one scroll of sections —
 *                 Inputs     the reference slots the agent asked for (ReferenceSlots, unchanged)
 *                 Outputs    the renders, as a grid of thumbnails (OutputsGrid)
 *                 Workflow   the reusable setup: the workflow pair, its installer, Save to Library
 *                 All files  the full folder tree, tick-to-delete, Add to Library (FileExplorer,
 *                            unchanged), folded by default
 *   Library     what the person kept, shared by EVERY chat (LibraryPanel, unchanged), including
 *               a slot's From Library door.
 *
 * ONE VIEWER. Opening anything — a render, a reference, the graph, a file in the tree, or a
 * thumbnail clicked in the conversation — shows it in the same FileViewer on the right, and the
 * sections narrow to a column beside it, exactly as the old rail did. Nothing opens by itself
 * (the thumbnail rule, agentd/artifacts.ts): the original bytes load only on a click.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import type { LibrarySaveOutcome } from '../../agentd/library'

import type { GpuWarmup } from './useGpuWarmup'

import type { Artifact } from '../../agentd/artifacts'
import type { LibraryItem } from '../../agentd/library'
import type { Slot } from '../../agentd/reference-slots'
import { useApp } from '../../state/store'
import { ActiveRunStrip } from './ActiveRunStrip'
import { FileExplorer } from './FileExplorer'
import { LibraryPanel } from '../library/LibraryPanel'
import { ReferenceSlots } from './ReferenceSlots'
import { FileViewer } from './FileViewer'
import { OutputsGrid } from './OutputsGrid'
import { WorkflowPanel } from './WorkflowPanel'
import { WorkspaceSection } from './WorkspaceSection'
import { StudioTopBar, type StudioPanel } from './StudioTopBar'
import { useStudioState } from './useStudioState'
import { collectWorkflows } from '../workflows/WorkflowCard'

import './studio.css'

const MEDIA = new Set(['image', 'video', 'audio'])
const inReferences = (a: Artifact): boolean => /[\\/]references[\\/]/.test(a.path)

export function StudioDashboard({
  client,
  gpu,
  running,
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
  onUseWorkflow,
  onRunAgain,
  onUseTemplate,
  onSaveTemplate,
}: {
  client: AgentdClient | undefined
  gpu: GpuWarmup
  running: boolean
  /** Everything the agent wrote this session — the Workspace's whole content. */
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
  /** The chat the Library's "Use" lands in. */
  sessionKey: string
  /** Bumped whenever the workspace changes, so the Library re-reads its catalogue. */
  workspaceVersion: number
  /** Hand a Library workflow to the agent in this chat. */
  onUseWorkflow: (item: LibraryItem) => void
  /** Start a new conversation around a Library workflow. */
  onRunAgain: (item: LibraryItem) => void
  /** Start a new conversation from a Library template. */
  onUseTemplate: (item: LibraryItem) => void
  /** Keep every workflow of this chat as one template (asks for its name first). */
  onSaveTemplate?: () => void
}) {
  const state = useStudioState(client, running)
  const selectedPath = useApp((s) => s.selectedArtifactPath)
  const setSelectedPath = useApp((s) => s.selectArtifact)
  const selectionSeq = useApp((s) => s.selectionSeq)
  /* WHICH TAB. Local, not store, state: nothing outside this column reads it. */
  const [panel, setPanel] = useState<StudioPanel>('workspace')
  /* A slot that asked for a Library reference (the From Library door on a slot): the Library
     opens with that role preselected and a line saying what it is waiting for. */
  const [targetRole, setTargetRole] = useState('')

  const made = useMemo(() => artifacts.filter((a) => !inReferences(a)), [artifacts])
  const outputs = useMemo(() => made.filter((a) => MEDIA.has(a.kind)), [made])
  const workflowSide = useMemo(() => made.filter((a) => !MEDIA.has(a.kind)), [made])
  const workflowCount = useMemo(
    () => collectWorkflows(workflowSide.filter((a) => !/^install_/i.test(a.name))).length,
    [workflowSide],
  )

  // SELECT BY PATH, RESOLVE BY LOOKUP: the same file is re-declared as later turns touch it, and
  // holding the object would pin whichever copy was clicked.
  const selected = useMemo(
    () => artifacts.find((a) => a.path === selectedPath) || null,
    [artifacts, selectedPath],
  )

  // A selection from another chat does not apply here.
  useEffect(() => {
    if (selectedPath && !artifacts.some((a) => a.path === selectedPath)) setSelectedPath('')
  }, [artifacts, selectedPath, setSelectedPath])

  /* EVERY PICK SHOWS THE PICKED FILE — from the conversation, a slot, a tile or the tree. It is
     always a Workspace file, so the Workspace comes forward. Keyed to the pick itself
     (selectionSeq), so a second click on the same file still works from the Library tab. */
  useEffect(() => {
    if (selectedPath) setPanel('workspace')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionSeq])

  /* THE AGENT ASKED FOR A PHOTO: the Workspace (where the slot is) comes forward, once, when an
     empty slot appears. A saved chat's slots arriving with its history are its opening state,
     not a new ask — `settled` marks the first time this chat had anything to show. */
  const emptySlots = slots.filter((s) => !s.file).length
  const seenEmpty = useRef(emptySlots)
  const settled = useRef(false)
  useEffect(() => {
    settled.current = false
    setTargetRole('')
    setPanel('workspace')
  }, [sessionKey])
  useEffect(() => {
    if (!settled.current) {
      if (artifacts.length || slots.length) settled.current = true
    } else if (emptySlots > seenEmpty.current) {
      setPanel('workspace')
    }
    seenEmpty.current = emptySlots
  }, [emptySlots, artifacts.length, slots.length, sessionKey])

  const hasInputs = slots.length > 0 || freeReferences.length > 0
  const filled = slots.length - emptySlots

  return (
    <div className="st-dash">
      <StudioTopBar
        state={state}
        client={client}
        gpu={gpu}
        credits={credits}
        onCredits={onCredits}
        panel={panel}
        attention={emptySlots > 0}
        onPanel={(p) => {
          setPanel(p)
          if (p !== 'library') setTargetRole('')
        }}
      />
      <ActiveRunStrip state={state} client={client} />

      <div className="st-body">
        {panel === 'library' ? (
          <LibraryPanel
            client={client}
            sessionKey={sessionKey}
            slots={slots}
            running={running}
            workspaceVersion={workspaceVersion}
            targetRole={targetRole}
            onClearTarget={() => {
              setTargetRole('')
              setPanel('workspace')
            }}
            onUseWorkflow={onUseWorkflow}
            onRunAgain={onRunAgain}
            onUseTemplate={onUseTemplate}
          />
        ) : (
          <>
            <div className={`ws${selected ? ' is-narrow' : ''}`}>
              {!artifacts.length && !hasInputs ? (
                <div className="op-empty">
                  <b>Nothing here yet</b>
                  <p>
                    This chat&rsquo;s photos, renders and workflow collect here as Penguin works. When
                    it needs a photo — a face, a product, a first frame — it asks for it here.
                  </p>
                </div>
              ) : (
                <>
                  {/* INPUTS FIRST, because a run cannot start while one is missing — and only
                      once the agent has asked for something, never as an empty upload box. */}
                  {hasInputs && (
                    <WorkspaceSection
                      title="Inputs"
                      count={slots.length ? `${filled} of ${slots.length}` : String(freeReferences.length)}
                      attention={emptySlots > 0}
                    >
                      <ReferenceSlots
                        slots={slots}
                        free={freeReferences}
                        disabled={referencesDisabled}
                        onAdd={onAddReference}
                        onOpen={(a) => setSelectedPath(a.path)}
                        onFromLibrary={(role) => {
                          setTargetRole(role)
                          setPanel('library')
                        }}
                      />
                    </WorkspaceSection>
                  )}
                  <WorkspaceSection title="Outputs" count={outputs.length ? String(outputs.length) : undefined}>
                    <OutputsGrid
                      outputs={outputs}
                      selectedPath={selectedPath}
                      onOpen={(a) => setSelectedPath(a.path)}
                      onDelete={onDeleteFiles}
                      onAddToLibrary={onAddToLibrary}
                      deletionDisabled={deletionDisabled}
                    />
                  </WorkspaceSection>
                  <WorkspaceSection title="Workflow" count={workflowCount ? String(workflowCount) : undefined}>
                    <WorkflowPanel
                      files={workflowSide}
                      onDelete={onDeleteFiles}
                      onAddToLibrary={onAddToLibrary}
                      onSaveTemplate={onSaveTemplate}
                      onOpen={(a) => setSelectedPath(a.path)}
                      deletionDisabled={deletionDisabled}
                    />
                  </WorkspaceSection>
                  {/* The references are Inputs above, so the tree lists what the agent MADE:
                      workflows, renders, installers. Unchanged from the old rail. */}
                  <WorkspaceSection title="All files" count={made.length ? String(made.length) : undefined} defaultOpen={false}>
                    <FileExplorer
                      artifacts={made}
                      selected={selected}
                      onSelect={(a) => setSelectedPath(a.path)}
                      onDelete={onDeleteFiles}
                      onAddToLibrary={onAddToLibrary}
                      deletionDisabled={deletionDisabled}
                    />
                  </WorkspaceSection>
                </>
              )}
            </div>

            {/* THE PANE IS NOT RENDERED WHEN NOTHING IS SELECTED — the sections take the room. */}
            {selected && (
              <main className="st-view">
                {/* KEYED BY PATH, so picking a different file MOUNTS A NEW VIEWER instead of
                    handing the old one new props — an <img> otherwise goes on painting the
                    previous render until the new bytes land. */}
                <FileViewer key={selected.path} file={selected} onClose={() => setSelectedPath('')} />
              </main>
            )}
          </>
        )}
      </div>
    </div>
  )
}
