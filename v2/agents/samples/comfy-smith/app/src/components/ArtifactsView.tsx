/* Everything the agent has made, not just the newest thing.
 *
 * The chat pane shows the CURRENT workflow because that is what the conversation is about. This
 * view exists for the other question — "which of the six did I like?" — which a transcript
 * answers badly and a list answers instantly. It also puts the rendered images next to the
 * graphs that produced them, because a workflow is judged by its output, not its JSON.
 */

import { useCallback, useEffect, useState } from 'react'
import type { AgentdClient } from '@agentd/client'
import { useWhenOpen } from '../agentd'
import { WorkflowPanel, type Workflow } from './WorkflowPanel'

interface Entry {
  name: string
  path: string
  size: number
}

export function ArtifactsView({
  client,
  listWorkspace,
  invoke,
  refreshKey,
}: {
  client: AgentdClient
  listWorkspace: (path?: string) => Promise<Entry[]>
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
  /** Changes whenever a tool that writes files finishes, so the list follows the conversation. */
  refreshKey: unknown
}) {
  const [workflows, setWorkflows] = useState<Entry[]>([])
  const [outputs, setOutputs] = useState<Entry[]>([])
  const [selected, setSelected] = useState<Workflow | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    // Each folder is optional — nothing has been built yet on a fresh install, and an empty
    // list is the honest rendering of that. A failure that is NOT "no such folder" is shown.
    const read = async (folder: string): Promise<Entry[]> => {
      try {
        return await listWorkspace(folder)
      } catch (e) {
        if (!/not found|no such|ENOENT/i.test(String(e))) setError(String(e))
        return []
      }
    }
    setWorkflows((await read('workflows')).filter((f) => f.name.endsWith('.json')))
    setOutputs((await read('outputs')).filter((f) => /\.(png|jpg|jpeg|webp|gif|mp4|webm)$/i.test(f.name)))
  }, [listWorkspace])

  useWhenOpen(client, load)

  useEffect(() => {
    if (client.connected) void load()
  }, [client, load, refreshKey])

  const open = async (entry: Entry) => {
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
        {!workflows.length && <p className="panel-empty">Nothing built yet.</p>}
        <ul className="files">
          {workflows.map((f) => (
            <li key={f.path}>
              <button
                className={selected?.path === f.path ? 'on' : ''}
                onClick={() => void open(f)}
              >
                <span className="f-name">{f.name}</span>
                <span className="f-size">{kb(f.size)}</span>
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

function kb(size: number): string {
  if (!size) return ''
  return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`
}
