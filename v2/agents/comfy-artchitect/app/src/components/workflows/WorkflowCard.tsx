/* One workflow, as a card with its two files.
 *
 * A WORKFLOW IS TWO FILES, and that is the detail this card exists to make obvious.
 * `comfy_emit` writes `<name>.api.json` (what `POST /prompt` accepts, and the only one that runs)
 * and `<name>.json` (what the browser imports). They are the same graph in two encodings, they are
 * NOT interchangeable, and pasting the wrong one into the wrong place fails in a way that reads
 * like a corrupt file. So they are shown as one card with two labelled ways out, rather than as
 * two files that happen to share a prefix.
 *
 * GENERIC OVER THE FILE TYPE. The conversation header pairs the current chat's plain artifacts;
 * the My creations screen pairs library files that also carry a delete path. Both are the same
 * pairing, so `collectWorkflows` takes whatever extends Artifact and hands the same objects back.
 */

import './workflows.css'

import { Download, FileJson, Play, Trash2 } from 'lucide-react'

import { fileUrl, humanSize, type Artifact } from '../../agentd/artifacts'

/** One workflow: the name it was emitted under, and whichever of its two files exist. */
export interface Workflow<A extends Artifact = Artifact> {
  name: string
  /** `<name>.api.json` — what runs. */
  api?: A
  /** `<name>.json` — what the ComfyUI browser imports. */
  ui?: A
}

/** `flux-portrait.api.json` -> `flux-portrait`, and the same for the UI twin.
 *
 *  `.api.json` is tested FIRST: `.json` also matches the tail of `.api.json`, so the other order
 *  would file every API graph under a name ending in ".api" and split each workflow in two. */
function split(name: string): { base: string; which: 'api' | 'ui' } | null {
  const lower = name.toLowerCase()
  if (lower.endsWith('.api.json')) return { base: name.slice(0, -'.api.json'.length), which: 'api' }
  if (lower.endsWith('.json')) return { base: name.slice(0, -'.json'.length), which: 'ui' }
  return null
}

/** Artifacts -> workflows, newest first.
 *
 *  LAST WINS per file. Iterating is fixing: the same name is emitted repeatedly, and the shelf
 *  should show where a workflow ENDED UP rather than every step it took to get there. The
 *  transcript is where the history lives. */
export function collectWorkflows<A extends Artifact>(artifacts: A[]): Workflow<A>[] {
  const byName = new Map<string, Workflow<A>>()
  for (const a of artifacts) {
    const parts = split(a.name)
    if (!parts) continue
    const wf = byName.get(parts.base) || { name: parts.base }
    wf[parts.which] = a
    byName.delete(parts.base) // re-insert so the most recently touched sorts newest
    byName.set(parts.base, wf)
  }
  return [...byName.values()].reverse()
}

/** Both of a workflow's files, for whoever needs to act on the whole thing. */
export function workflowFiles<A extends Artifact>(wf: Workflow<A>): A[] {
  return [wf.api, wf.ui].filter((f): f is A => Boolean(f))
}

export function WorkflowCard<A extends Artifact>({
  wf,
  onDelete,
}: {
  wf: Workflow<A>
  /** Drawn as a trash button when given. The card asks; the screen decides what asking means. */
  onDelete?: (wf: Workflow<A>) => void
}) {
  const bytes = (wf.api?.size || 0) + (wf.ui?.size || 0)
  return (
    <div className="wf-card">
      <div className="wf-card-head">
        <span className="wf-card-ico">
          <FileJson size={16} strokeWidth={1.7} />
        </span>
        <div className="wf-card-text">
          <span className="wf-card-title">{wf.name}</span>
          <span className="wf-card-meta">
            {[humanSize(bytes), wf.api && wf.ui ? 'run + import files' : wf.api ? 'run file only' : 'import file only']
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
        {onDelete && (
          <button
            className="wf-card-del"
            onClick={() => onDelete(wf)}
            title="Delete this workflow"
            aria-label={`Delete ${wf.name}`}
          >
            <Trash2 size={14} strokeWidth={1.8} />
          </button>
        )}
      </div>

      {/* TWO FILES, TWO JOBS, SAID OUT LOUD. `download` rather than a plain link: a browser asked
          to navigate to JSON renders it as a wall of text in a tab, and what the user wants is the
          file — to drop into ComfyUI, or to keep. Only the buttons for files that exist are drawn. */}
      <div className="wf-card-files">
        {wf.api && (
          <a className="wf-file" href={fileUrl(wf.api.path)} download={wf.api.name}>
            <Play size={13} strokeWidth={1.8} />
            <span className="wf-file-text">
              <span className="wf-file-name">Run file · .api.json</span>
              <span className="wf-file-sub">send to the server</span>
            </span>
            <Download size={13} strokeWidth={1.7} className="wf-file-go" />
          </a>
        )}
        {wf.ui && (
          <a className="wf-file" href={fileUrl(wf.ui.path)} download={wf.ui.name}>
            <FileJson size={13} strokeWidth={1.8} />
            <span className="wf-file-text">
              <span className="wf-file-name">ComfyUI file · .json</span>
              <span className="wf-file-sub">drag into the canvas</span>
            </span>
            <Download size={13} strokeWidth={1.7} className="wf-file-go" />
          </a>
        )}
      </div>
    </div>
  )
}
