/* One workflow as the Workspace shows it: the card with its two files, a row of actions, and —
 * when a validate exported one — the installer for the person's own ComfyUI.
 *
 * ONE COMPONENT FOR EVERY PLACE A WORKFLOW IS SHOWN. The Workspace's Workflow tab and the
 * Library draw this same block, so a workflow looks the same wherever it is kept; only the
 * action row differs (Save to Library in a chat, Use / Run again in the Library), and the
 * screen hands that row in. A second look-alike card is how the Library ended up showing a
 * name and one button while the Workspace showed both files and their installer.
 */

import { Download } from 'lucide-react'
import type { ReactNode } from 'react'

import { fileUrl, type Artifact } from '../../agentd/artifacts'
import { collectWorkflows, WorkflowCard, type Workflow } from './WorkflowCard'

/** `install_stills.py` / `install_stills.manifest.json` — the installer a validate exported. */
export const INSTALLER = /^install_(.+?)(\.manifest\.json|\.py)$/i

/** A chat's workflows as a template keeps them: each role's run file (required), ComfyUI file
 *  and installer files. `files` are the chat's workflow-folder files. */
export function chatWorkflowFiles<A extends Artifact>(
  files: A[],
): Array<{ role: string; api: A; ui?: A; installer: A[] }> {
  const graphs = collectWorkflows(files.filter((f) => !INSTALLER.test(f.name)))
  return graphs
    .filter((wf) => wf.api)
    .map((wf) => ({ role: wf.name, api: wf.api as A, ui: wf.ui, installer: installersFor(wf.name, files) }))
}

/** The installer files among `files` that belong to workflow `name`. */
export function installersFor<A extends Artifact>(name: string, files: A[]): A[] {
  return files.filter((f) => INSTALLER.exec(f.name)?.[1] === name)
}

export function WorkflowItem<A extends Artifact>({
  wf,
  installers,
  meta,
  onDelete,
  actions,
  children,
}: {
  wf: Workflow<A>
  /** This workflow's `install_*` files, drawn as the "For your own ComfyUI" block. */
  installers: A[]
  /** Extra facts for the card's meta line (a Library version, its slots). */
  meta?: string
  onDelete?: (wf: Workflow<A>) => void
  /** The screen's own buttons, drawn under the files. */
  actions?: ReactNode
  /** Anything the screen adds under the installer (a Library note). */
  children?: ReactNode
}) {
  return (
    <section className="wp-item">
      <WorkflowCard wf={wf} meta={meta} onDelete={onDelete} />
      {actions && <div className="wp-row">{actions}</div>}
      {installers.length > 0 && (
        <div className="wp-inst">
          <span className="wp-inst-title">For your own ComfyUI</span>
          <span className="wp-inst-sub">
            An installer that sets up this workflow&rsquo;s models and nodes on your machine.
          </span>
          <div className="wp-row">
            {installers.map((f) => (
              <a key={f.path} className="wp-btn" href={fileUrl(f.path)} download={f.name}>
                <Download size={14} strokeWidth={1.9} /> {f.name}
              </a>
            ))}
          </div>
        </div>
      )}
      {children}
    </section>
  )
}

export default WorkflowItem
