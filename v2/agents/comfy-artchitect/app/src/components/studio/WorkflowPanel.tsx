/* The Workflow tab — the reusable setup this chat produced, which is the thing Penguin sells.
 *
 * WHAT IS SHOWN. Every workflow role the chat emitted (`comfy_emit` writes a run file and an
 * import file per role; WorkflowCard pairs them), each with the renders beside it, and — when a
 * passing validate exported one — its portable installer: `install_<role>.py` plus its manifest,
 * which reproduce the dependencies on the person's own ComfyUI.
 *
 * NOTHING NEW CAN BE DONE HERE, only found faster. Save to Library, delete (one warning, then the
 * daemon; refused mid-run like everywhere else) and both downloads are the same handlers the file
 * tree and My creations always used. Running a kept workflow again is the Library's Run again —
 * a workflow has to be in the Library to be run from another conversation.
 */

import { BookmarkPlus, FileCode2, LayoutTemplate } from 'lucide-react'
import { useMemo, useState } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import { DeleteFilePrompt } from '../creations/DeleteFilePrompt'
import { collectWorkflows, workflowFiles, type Workflow } from '../workflows/WorkflowCard'
import { INSTALLER, WorkflowItem } from '../workflows/WorkflowItem'

export function WorkflowPanel({
  files,
  onDelete,
  onAddToLibrary,
  onSaveTemplate,
  onOpen,
  deletionDisabled = '',
}: {
  /** This chat's workflow-folder files (App's merged list, references excluded). */
  files: Artifact[]
  onDelete?: (paths: string[]) => Promise<void>
  onAddToLibrary?: (paths: string[]) => Promise<string>
  /** Keep ALL of this chat's workflows as one template (the prompt asks its name). */
  onSaveTemplate?: () => void
  /** Show a file (the graph's JSON, the installer) in the viewer beside this panel. */
  onOpen: (a: Artifact) => void
  deletionDisabled?: string
}) {
  const { workflows, installers } = useMemo(() => {
    const inst = new Map<string, Artifact[]>()
    const rest: Artifact[] = []
    for (const f of files) {
      const m = INSTALLER.exec(f.name)
      if (m) inst.set(m[1], [...(inst.get(m[1]) || []), f])
      else rest.push(f)
    }
    return { workflows: collectWorkflows(rest), installers: inst }
  }, [files])

  const [doomed, setDoomed] = useState<Workflow | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [notice, setNotice] = useState('')

  const save = async (wf: Workflow): Promise<void> => {
    if (!onAddToLibrary) return
    try {
      // The installer travels with the graph: a kept workflow is the whole reusable setup.
      const inst = installers.get(wf.name) || []
      setNotice(await onAddToLibrary([...workflowFiles(wf), ...inst].map((f) => f.path)))
    } catch (e) {
      setNotice(`Could not add to the Library: ${String((e as Error)?.message || e)}`)
    }
    setTimeout(() => setNotice(''), 4000)
  }
  const doDelete = async (): Promise<void> => {
    if (!doomed || !onDelete) return
    setDeleting(true)
    setDeleteError('')
    try {
      await onDelete(workflowFiles(doomed).map((f) => f.path))
      setDoomed(null)
    } catch (e) {
      setDeleteError(String((e as Error)?.message || e))
    } finally {
      setDeleting(false)
    }
  }

  if (!workflows.length) {
    return (
      <p className="ws-empty">
        The workflow Penguin builds shows up here. Save it to your Library to run it again from any
        chat, or download it for your own ComfyUI.
      </p>
    )
  }

  return (
    <div className="wp">
      {notice && <p className="op-note">{notice}</p>}
      {onSaveTemplate && (
        <button type="button" className="wp-btn is-primary wp-template" onClick={onSaveTemplate}>
          <LayoutTemplate size={14} strokeWidth={1.9} /> Save as template to reuse
        </button>
      )}
      {workflows.map((wf) => (
        <WorkflowItem
          key={wf.name}
          wf={wf}
          installers={installers.get(wf.name) || []}
          onDelete={
            onDelete && !deletionDisabled
              ? (w) => {
                  setDeleteError('')
                  setDoomed(w)
                }
              : undefined
          }
          actions={
            <>
              {onAddToLibrary && (
                <button type="button" className="wp-btn is-primary" onClick={() => void save(wf)}>
                  <BookmarkPlus size={14} strokeWidth={1.9} /> Save to Library
                </button>
              )}
              {wf.api && (
                <button type="button" className="wp-btn" onClick={() => onOpen(wf.api!)}>
                  <FileCode2 size={14} strokeWidth={1.9} /> View graph
                </button>
              )}
            </>
          }
        />
      ))}
      {deletionDisabled && <p className="wp-hint">{deletionDisabled} to delete a workflow.</p>}

      {doomed && (
        <DeleteFilePrompt
          names={workflowFiles(doomed).map((f) => f.name)}
          busy={deleting}
          error={deleteError}
          onDelete={() => void doDelete()}
          onClose={() => setDoomed(null)}
        />
      )}
    </div>
  )
}

export default WorkflowPanel
