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

import { BookmarkPlus, Download, FileCode2 } from 'lucide-react'
import { useMemo, useState } from 'react'

import { fileUrl, type Artifact } from '../../agentd/artifacts'
import { DeleteFilePrompt } from '../creations/DeleteFilePrompt'
import { collectWorkflows, WorkflowCard, workflowFiles, type Workflow } from '../workflows/WorkflowCard'

/** `install_stills.py` / `install_stills.manifest.json` — the installer a validate exported. */
const INSTALLER = /^install_(.+?)(\.manifest\.json|\.py)$/i

export function WorkflowPanel({
  files,
  onDelete,
  onAddToLibrary,
  onOpen,
  deletionDisabled = '',
}: {
  /** This chat's workflow-folder files (App's merged list, references excluded). */
  files: Artifact[]
  onDelete?: (paths: string[]) => Promise<void>
  onAddToLibrary?: (paths: string[]) => Promise<string>
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
      setNotice(await onAddToLibrary(workflowFiles(wf).map((f) => f.path)))
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
      {workflows.map((wf) => {
        const inst = installers.get(wf.name) || []
        return (
          <section key={wf.name} className="wp-item">
            {/* The card's own bookmark is left off: the labelled Save button below is the same
                action, and two saves side by side read as two different things. */}
            <WorkflowCard
              wf={wf}
              onDelete={
                onDelete && !deletionDisabled
                  ? (w) => {
                      setDeleteError('')
                      setDoomed(w)
                    }
                  : undefined
              }
            />
            <div className="wp-row">
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
            </div>
            {inst.length > 0 && (
              <div className="wp-inst">
                <span className="wp-inst-title">For your own ComfyUI</span>
                <span className="wp-inst-sub">
                  An installer that sets up this workflow&rsquo;s models and nodes on your machine.
                </span>
                <div className="wp-row">
                  {inst.map((f) => (
                    <a key={f.path} className="wp-btn" href={fileUrl(f.path)} download={f.name}>
                      <Download size={14} strokeWidth={1.9} /> {f.name}
                    </a>
                  ))}
                </div>
              </div>
            )}
          </section>
        )
      })}
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
