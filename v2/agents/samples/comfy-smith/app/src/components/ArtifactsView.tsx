/* Everything the agent has made, not just the newest thing.
 *
 * The chat pane shows the CURRENT workflow because that is what the conversation is about. This
 * view exists for the other question — "which of the six did I like?" — which a transcript
 * answers badly and a list answers instantly. It also puts the rendered images next to the
 * graphs that produced them, because a workflow is judged by its output, not its JSON.
 */

import { useCallback, useEffect, useState } from 'react'
import type { AgentdClient } from '@agentd/client'
import { useWhenOpen, type WorkflowEntry } from '../agentd'
import { WorkflowPanel, type Workflow } from './WorkflowPanel'

interface Entry {
  name: string
  path: string
  size: number
}

export function ArtifactsView({
  client,
  listWorkspace,
  listWorkflows,
  invoke,
  refreshKey,
}: {
  client: AgentdClient
  listWorkspace: (path?: string) => Promise<Entry[]>
  listWorkflows: () => Promise<WorkflowEntry[]>
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
  /** Changes whenever a tool that writes files finishes, so the list follows the conversation. */
  refreshKey: unknown
}) {
  const [workflows, setWorkflows] = useState<WorkflowEntry[]>([])
  const [outputs, setOutputs] = useState<Entry[]>([])
  const [selected, setSelected] = useState<Workflow | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    // TWO DIFFERENT QUESTIONS, asked of two different places on purpose.
    //
    // Workflows come from the agent's OWN tool, because it resolves the same workspace the agent
    // writes to; the gateway can resolve a different one for a signed-in window and report an
    // empty folder over a file that exists.
    //
    // Renders come from the gateway, because they are just files and there is no tool that lists
    // them — and unlike the workflows, nothing depends on which root they came from.
    try {
      setWorkflows(await listWorkflows())
      setError('')
    } catch (e) {
      // Shown, not swallowed. An empty list and a failed question look identical on screen, and
      // only one of them means "nothing has been built".
      setError(String(e))
      setWorkflows([])
    }
    try {
      const files = await listWorkspace('outputs')
      setOutputs(files.filter((f) => /\.(png|jpg|jpeg|webp|gif|mp4|webm)$/i.test(f.name)))
    } catch (e) {
      // A missing outputs/ is the normal state before the first render — not worth a banner.
      if (!/not found|no such|ENOENT/i.test(String(e))) setError(String(e))
      setOutputs([])
    }
  }, [listWorkspace, listWorkflows])

  useWhenOpen(client, load)

  useEffect(() => {
    if (client.connected) void load()
  }, [client, load, refreshKey])

  const open = async (entry: { name: string; path: string }) => {
    setError('')
    try {
      // The agent's own `read`, not GET /file: this path is already authorised by the
      // connection, while /file wants a token and an absolute path.
      const json = await invoke('read', { path: entry.path })
      setSelected({ name: entry.name, path: entry.path, json })
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="view artifacts">
      <div className="artifact-list">
        <header className="view-head">
          <h1>Workflows</h1>
          <span className="muted">{workflows.length}</span>
        </header>
        {error && <p className="panel-error">{error}</p>}
        {/* Only when the question SUCCEEDED and came back empty. Saying "nothing built yet"
            after a failed lookup is a claim about the workspace we did not manage to read. */}
        {!error && !workflows.length && <p className="panel-empty">Nothing built yet.</p>}
        <ul className="files">
          {workflows.map((f) => (
            <li key={f.path}>
              <button
                className={selected?.path === f.path ? 'on' : ''}
                onClick={() => void open(f)}
              >
                <span className="f-name">{f.name}</span>
                {/* What it IS, not how big it is: only the API format can be run, and that is
                    the difference the Run button depends on. */}
                <span className="f-size">{_shape(f)}</span>
              </button>
            </li>
          ))}
        </ul>

        {outputs.length > 0 && (
          <>
            <header className="view-head">
              <h1>Renders</h1>
              <span className="muted">{outputs.length}</span>
            </header>
            <div className="renders">
              {outputs.map((f) => (
                <a
                  key={f.path}
                  href={client.fileUrl(f.path)}
                  target="_blank"
                  rel="noreferrer"
                  title={f.name}
                >
                  <img src={client.fileUrl(f.path)} alt={f.name} />
                </a>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="artifact-detail">
        <WorkflowPanel workflow={selected} invoke={invoke} />
      </div>
    </div>
  )
}

/** A workflow's shape, in three words. `''` means the file would not parse — worth SAYING,
 *  because an unreadable workflow is the one you most need to know about and it is otherwise
 *  indistinguishable from a healthy one in a list of filenames. */
function _shape(entry: WorkflowEntry): string {
  if (!entry.format) return 'unreadable'
  return entry.format === 'api' ? `${entry.nodes} nodes · runnable` : `${entry.nodes} nodes · UI`
}
