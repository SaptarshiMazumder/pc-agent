/* The server, its models and its nodes — read directly, without spending a conversation.
 *
 * These are the SAME tools the agent calls, invoked from a button: `client.invokeTool`, no chat
 * turn, no model, no tokens. That is the difference between an app and a transcript. "Which
 * checkpoints are on that pod?" is a lookup, not a question worth a reasoning model, and
 * answering it here means the user can check before asking rather than after being told wrong.
 *
 * It is also the honest debugging surface: when the agent says it cannot reach the server, this
 * is where you find out whether that is true.
 */

import { useCallback, useEffect, useState } from 'react'

export type Inspector = 'server' | 'models' | 'nodes'

const SPEC: Record<
  Inspector,
  { title: string; blurb: string; tool: string; arg?: string; placeholder?: string }
> = {
  server: {
    title: 'Server',
    blurb: 'Live from /system_stats — the GPU, the VRAM free right now, and the queue.',
    tool: 'comfy_server',
  },
  models: {
    title: 'Models',
    blurb: 'What is on that machine. Leave the box empty for the folder list.',
    tool: 'comfy_models',
    arg: 'folder',
    placeholder: 'checkpoints, loras, vae…',
  },
  nodes: {
    title: 'Nodes',
    blurb: 'Node classes installed on the server. Search, or name one for its full input schema.',
    tool: 'comfy_nodes',
    arg: 'search',
    placeholder: 'sampler, upscale, controlnet…',
  },
}

export function InspectorView({
  kind,
  invoke,
  connected,
}: {
  kind: Inspector
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
  connected: boolean
}) {
  const spec = SPEC[kind]
  const [query, setQuery] = useState('')
  const [out, setOut] = useState('')
  const [busy, setBusy] = useState(false)

  const run = useCallback(
    async (value: string) => {
      setBusy(true)
      try {
        setOut(await invoke(spec.tool, spec.arg && value ? { [spec.arg]: value } : {}))
      } catch (e) {
        // The tool's own failure text is the useful part and it is shown verbatim — replacing it
        // with "failed to load" would throw away the sentence that says which setting is empty.
        setOut(String(e))
      } finally {
        setBusy(false)
      }
    },
    [invoke, spec],
  )

  // Load the default view on arrival; the search box re-runs it on demand.
  useEffect(() => {
    setOut('')
    setQuery('')
    if (connected) void run('')
  }, [kind, connected, run])

  return (
    <div className="view inspector">
      <header className="view-head">
        <div>
          <h1>{spec.title}</h1>
          <p className="muted">{spec.blurb}</p>
        </div>
        <button className="ghost" disabled={busy} onClick={() => void run(query)}>
          {busy ? 'Reading…' : 'Refresh'}
        </button>
      </header>

      {spec.arg && (
        <div className="inspector-search">
          <input
            value={query}
            placeholder={spec.placeholder}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void run(query)}
          />
          <button className="primary" disabled={busy} onClick={() => void run(query)}>
            Look up
          </button>
        </div>
      )}

      <pre className="readout">{out || (busy ? 'Reading…' : 'Nothing yet.')}</pre>
    </div>
  )
}
