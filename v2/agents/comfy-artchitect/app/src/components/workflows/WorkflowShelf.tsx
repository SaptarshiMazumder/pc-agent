/* Every workflow this agent has emitted, in one place.
 *
 * WHY THE CONVERSATION IS NOT ENOUGH. A workflow is built by iterating — emit, run, read the
 * server's complaint, change one thing, emit again. After six rounds the transcript holds six
 * versions of the same graph, and the one you want is the one you have to scroll for. This screen
 * is the shelf: newest first, each with both of its files.
 *
 * A WORKFLOW IS TWO FILES, and that is the detail this screen exists to make obvious.
 * `comfy_emit` writes `<name>.api.json` (what `POST /prompt` accepts, and the only one that runs)
 * and `<name>.json` (what the browser imports). They are the same graph in two encodings, they are
 * NOT interchangeable, and pasting the wrong one into the wrong place fails in a way that reads
 * like a corrupt file. So they are shown as one card with two labelled ways out, rather than as
 * two files that happen to share a prefix.
 *
 * IT INVENTS NOTHING. Rows come from the artifacts the runs actually declared — no sample data, so
 * an empty shelf is a true statement about an agent that has not emitted anything yet.
 */

import './workflows.css'

import { Download, FileJson, Play } from 'lucide-react'

import { fileUrl, humanSize, type Artifact } from '../../agentd/artifacts'

/** One workflow: the name it was emitted under, and whichever of its two files exist. */
export interface Workflow {
  name: string
  /** `<name>.api.json` — what runs. */
  api?: Artifact
  /** `<name>.json` — what the ComfyUI browser imports. */
  ui?: Artifact
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
 *  transcript is where the history lives.
 *
 *  Exported so it can be tested, and so App.tsx can count workflows without rendering the screen. */
export function collectWorkflows(artifacts: Artifact[]): Workflow[] {
  const byName = new Map<string, Workflow>()
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

function Card({ wf }: { wf: Workflow }) {
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
            {[wf.api && wf.ui ? 'both formats' : wf.api ? 'API only' : 'import only', humanSize(bytes)]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
      </div>

      {/* TWO FILES, TWO JOBS, SAID OUT LOUD. `download` rather than a plain link: a browser asked
          to navigate to JSON renders it as a wall of text in a tab, and what the user wants is the
          file — to drop into ComfyUI, or to keep. Only the buttons for files that exist are drawn. */}
      <div className="wf-card-files">
        {wf.api && (
          <a className="wf-file" href={fileUrl(wf.api.path)} download={wf.api.name}>
            <Play size={13} strokeWidth={1.8} />
            <span className="wf-file-text">
              <span className="wf-file-name">API format</span>
              <span className="wf-file-sub">what the server runs</span>
            </span>
            <Download size={13} strokeWidth={1.7} className="wf-file-go" />
          </a>
        )}
        {wf.ui && (
          <a className="wf-file" href={fileUrl(wf.ui.path)} download={wf.ui.name}>
            <FileJson size={13} strokeWidth={1.8} />
            <span className="wf-file-text">
              <span className="wf-file-name">Import format</span>
              <span className="wf-file-sub">drag into ComfyUI</span>
            </span>
            <Download size={13} strokeWidth={1.7} className="wf-file-go" />
          </a>
        )}
      </div>
    </div>
  )
}

export default function WorkflowShelf({ artifacts }: { artifacts: Artifact[] }) {
  const workflows = collectWorkflows(artifacts)

  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">Workflows</h1>
          <p className="page-sub">
            {workflows.length === 0
              ? 'Nothing emitted yet'
              : `${workflows.length} workflow${workflows.length === 1 ? '' : 's'} · newest first`}
          </p>
        </div>
      </header>

      <div className="stage">
        <div className="stage-main">
          {workflows.length === 0 ? (
            /* THE EMPTY STATE SAYS WHAT TO DO. It is the screen most people see first — on the day
               they install the agent there is nothing here, and "no items" would waste that. */
            <p className="wf-empty">
              Ask for a workflow in the conversation — something like “a text-to-image workflow
              using what my instance already has”. Each one is written twice, once to run and once
              to import, and both land here.
            </p>
          ) : (
            <div className="wf-shelf">
              {workflows.map((wf) => (
                <Card key={wf.name} wf={wf} />
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
