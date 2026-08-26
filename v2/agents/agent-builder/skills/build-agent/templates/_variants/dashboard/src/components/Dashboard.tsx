/* The dashboard: panels that SHOW, instead of a conversation you have to ask.
 *
 * A TEMPLATE FILE, and the one place this variant differs from the chat template. Everything it
 * imports — the client, the styles' tokens — is the base skeleton's copy, landed in this same
 * tree at creation. The base is written once; this file only adds.
 *
 * WHAT A PANEL IS. A title, a fetch, and a rendering of whatever came back. The fetch calls the
 * agent's OWN tools (`client.invokeTool`) — no model, no tokens, no waiting on a conversation —
 * because a dashboard's whole argument is that the number is already on screen when you look.
 *
 * EDIT `PANELS`. The two below are placeholders that work in any agent so the window is alive
 * before a single tool exists; replace them with panels over this agent's real tools. A trading
 * monitor shows P&L; an ingest agent shows its queue. If a panel needs the MODEL rather than a
 * tool, that is what the chat view (still in the rail) is for.
 */

import { useCallback, useEffect, useState } from 'react'
import type { AgentdClient } from '@agentd/client'

import './dashboard.css'

/** One panel: what it is called, and how it gets its content. The fetch returns whatever your
 *  rendering understands — keep them next to each other in this file. */
interface PanelSpec {
  title: string
  /** How often to re-fetch, in seconds. 0 = only on load and the Refresh button. */
  every: number
  fetch: (client: AgentdClient) => Promise<unknown>
  render: (data: unknown) => JSX.Element
}

/* REPLACE THESE with panels over this agent's own tools:
 *
 *   fetch: (client) => client.invokeTool('get_cost_snapshot', { period: 'today' }),
 *   render: (d: any) => <span className="dash-big">${d.total.toFixed(2)}</span>,
 */
const PANELS: PanelSpec[] = [
  {
    title: 'Tools this agent has',
    every: 0,
    fetch: async (client) => {
      const res: any = await client.request('tools.list', {})
      return (res?.tools || []).map((t: any) => String(t.name || ''))
    },
    render: (data) => {
      const names = data as string[]
      return names.length ? (
        <ul className="dash-list">
          {names.slice(0, 8).map((n) => (
            <li key={n}>{n}</li>
          ))}
          {names.length > 8 && <li className="dash-dim">…and {names.length - 8} more</li>}
        </ul>
      ) : (
        <span className="dash-dim">no tools yet — this panel is a placeholder</span>
      )
    },
  },
  {
    title: 'Conversations',
    every: 0,
    fetch: async (client) => {
      const res: any = await client.request('sessions.list', {})
      return (res?.sessions || res?.rows || []).length
    },
    render: (data) => <span className="dash-big">{String(data)}</span>,
  },
]

function Panel({ spec, client, connected }: { spec: PanelSpec; client: AgentdClient; connected: boolean }) {
  const [data, setData] = useState<unknown>(undefined)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setBusy(true)
    spec
      .fetch(client)
      .then((d) => {
        setData(d)
        setError('')
      })
      // SHOWN IN THE PANEL, not swallowed: a tile that silently shows stale data is the
      // dashboard failure — the number looks fine and is from an hour ago.
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }, [spec, client])

  /* NOT BEFORE THE SOCKET IS OPEN. A fetch against a closed connection can only produce
     "not connected" — that is waiting, not an error, so the panel says so instead. Failures of a
     real call still land in `error`, red, exactly as before. */
  useEffect(() => {
    if (!connected) return
    load()
    if (!spec.every) return
    const t = setInterval(load, spec.every * 1000)
    return () => clearInterval(t)
  }, [load, spec.every, connected])

  return (
    <section className="dash-tile">
      <header className="dash-tile-head">
        <span className="dash-tile-title">{spec.title}</span>
        <button className="dash-refresh" onClick={load} disabled={busy} title="Refresh">
          {busy ? '…' : '↻'}
        </button>
      </header>
      <div className="dash-tile-body">
        {!connected ? (
          <span className="dash-dim">waiting for the daemon…</span>
        ) : error ? (
          <span className="dash-error">{error}</span>
        ) : data === undefined ? (
          <span className="dash-dim">loading…</span>
        ) : (
          spec.render(data)
        )}
      </div>
    </section>
  )
}

export default function Dashboard({ client, connected }: { client: AgentdClient; connected: boolean }) {
  return (
    <div className="dashboard">
      <div className="dashboard-grid">
        {PANELS.map((p) => (
          <Panel key={p.title} spec={p} client={client} connected={connected} />
        ))}
      </div>
    </div>
  )
}
