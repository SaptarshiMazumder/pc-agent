/* Everything this creation made, across stages — the other view of the studio, beside the stages:
 *
 *   Outputs    the renders, as a grid of thumbnails (OutputsGrid): open, download, Add to Library, delete
 *   Workflow   the reusable setup: the workflow pair, its installer, Save to Library, Save as template
 *   All files  the full folder tree, tick-to-delete, Add to Library (FileExplorer), folded by default
 */

import type { Artifact } from '../../agentd/artifacts'
import type { LibrarySaveOutcome } from '../../agentd/library'
import { FileExplorer } from './FileExplorer'
import { OutputsGrid } from './OutputsGrid'
import { WorkflowPanel } from './WorkflowPanel'
import { WorkspaceSection } from './WorkspaceSection'

export function WorkflowFilesView({
  outputs,
  workflowSide,
  made,
  workflowCount,
  selected,
  selectedPath,
  onOpen,
  onDeleteFiles,
  onAddToLibrary,
  onSaveTemplate,
  deletionDisabled,
}: {
  outputs: Artifact[]
  workflowSide: Artifact[]
  made: Artifact[]
  workflowCount: number
  selected: Artifact | null
  selectedPath: string
  onOpen: (a: Artifact) => void
  onDeleteFiles?: (paths: string[]) => Promise<void>
  onAddToLibrary?: (paths: string[]) => Promise<LibrarySaveOutcome>
  onSaveTemplate?: () => void
  deletionDisabled?: string
}) {
  return (
    <div className="wf">
      <WorkspaceSection title="Outputs" count={outputs.length ? String(outputs.length) : undefined}>
        <OutputsGrid outputs={outputs} selectedPath={selectedPath} onOpen={onOpen} onDelete={onDeleteFiles} onAddToLibrary={onAddToLibrary} deletionDisabled={deletionDisabled} />
      </WorkspaceSection>
      <WorkspaceSection title="Workflow" count={workflowCount ? String(workflowCount) : undefined}>
        <WorkflowPanel files={workflowSide} onDelete={onDeleteFiles} onAddToLibrary={onAddToLibrary} onSaveTemplate={onSaveTemplate} onOpen={onOpen} deletionDisabled={deletionDisabled} />
      </WorkspaceSection>
      <WorkspaceSection title="All files" count={made.length ? String(made.length) : undefined} defaultOpen={false}>
        <FileExplorer artifacts={made} selected={selected} onSelect={onOpen} onDelete={onDeleteFiles} onAddToLibrary={onAddToLibrary} deletionDisabled={deletionDisabled} />
      </WorkspaceSection>
    </div>
  )
}
