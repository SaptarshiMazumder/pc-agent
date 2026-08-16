/* The artifact, as a first-class object rather than a code block in a transcript.
 *
 * WHY THIS PANE EXISTS. The agent's real output is a file somebody imports into ComfyUI. Left in
 * the conversation it has to be scrolled back to, copied out, and guessed about — is this the
 * latest one? did the fix land? So: always the current workflow, always its validation state,
 * one click to save.
 *
 * VALIDATE AND RUN ARE DIRECT TOOL CALLS — `client.invokeTool`, no chat turn, no model, no
 * tokens. That is the difference between a button that does the thing and a button that asks an
 * LLM to do the thing: these answer directly and cost nothing, so pressing them is free.
 *
 * RUN IS THE ONE THAT MATTERS. Validation checks the graph against what the server has; running
 * it is the only thing that proves it works. Putting it on a button means the user can re-run a
 * workflow from last week without spending a conversation on it.
 */

import { useEffect, useState } from 'react'

export interface Workflow {
  name: string
  path: string
  json: string
}

type Check = { state: 'idle' | 'running' | 'ok' | 'bad'; message: string; what: string }

export function WorkflowPanel({
  workflow,
  invoke,
}: {
  workflow: Workflow | null
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
}) {
  const [check, setCheck] = useState<Check>({ state: 'idle', message: '', what: '' })

  // A new workflow invalidates the old verdict. Leaving a green tick from the PREVIOUS file
  // above a new one is the worst thing this pane could do — it certifies something unchecked.
  useEffect(
    () => setCheck({ state: 'idle', message: '', what: '' }),
    [workflow?.path, workflow?.json],
  )

  if (!workflow) {
    return (
      <div className="panel empty">
        <h2>No workflow yet</h2>
        <p>Ask for one and it appears here — with its validation state and a save button.</p>
      </div>
    )
  }

  const nodeCount = countNodes(workflow.json)

  /** One path for both buttons: they differ only in which tool answers and how long it takes. */
  const call = async (tool: string, what: string, bad: RegExp) => {
    setCheck({ state: 'running', message: '', what })
    try {
      const out = await invoke(tool, { path: workflow.path })
      setCheck({ state: bad.test(out) ? 'bad' : 'ok', message: out.trim(), what })
    } catch (e) {
      setCheck({ state: 'bad', message: String(e), what })
    }
  }

  const validate = () => call('validate_workflow', 'Validate', /\[x\]|error|invalid|missing/i)
  // A run reports its own failure in the text — including the server's node errors, which are
  // the actionable part and are shown verbatim rather than reduced to a red tick.
  const run = () => call('run_workflow', 'Run', /FAILED|REJECTED|could not|error/i)

  const save = () => {
    // Saved from the BROWSER, not written by the agent: this is the user taking a copy to
    // wherever their ComfyUI lives, which may not be this machine at all.
    const url = URL.createObjectURL(new Blob([workflow.json], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = workflow.name
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>{workflow.name}</h2>
          <span className="sub">{nodeCount === null ? 'not valid JSON' : `${nodeCount} nodes`}</span>
        </div>
        <div className="acts">
          <button className="ghost" onClick={validate} disabled={check.state === 'running'}>
            {check.state === 'running' && check.what === 'Validate' ? 'checking…' : 'Validate'}
          </button>
          <button className="ghost" onClick={run} disabled={check.state === 'running'}>
            {check.state === 'running' && check.what === 'Run' ? 'running…' : 'Run'}
          </button>
          <button className="prime" onClick={save}>
            Save
          </button>
        </div>
      </div>

      {check.state !== 'idle' && check.state !== 'running' && (
        <pre className={`verdict ${check.state}`}>{check.message || (check.state === 'ok' ? 'OK' : '')}</pre>
      )}

      <pre className="json">{workflow.json}</pre>
    </div>
  )
}

/** ComfyUI's API format is a flat object of node-id -> node. Null when it will not parse, which
 *  the header reports rather than hiding — an unparseable workflow is the thing you most need
 *  to know about, and it is exactly when a node count would otherwise read "0". */
function countNodes(json: string): number | null {
  try {
    const data = JSON.parse(json)
    if (Array.isArray(data?.nodes)) return data.nodes.length // UI-export format
    if (data && typeof data === 'object') return Object.keys(data).length // API format
    return null
  } catch {
    return null
  }
}
