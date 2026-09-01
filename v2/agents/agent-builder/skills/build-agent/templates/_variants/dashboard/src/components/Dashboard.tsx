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
 * EDIT `PANELS`. Everything below is a PLACEHOLDER so the window is alive before a single tool
 * exists; the widgets it renders live in `widgets/` and are yours to reuse, restyle or delete
 * (read `widgets/README.md`). A cost agent shows spend; an ingest agent shows its queue. If a
 * panel needs the MODEL rather than a tool, that is what the agent panel on the right is for.
 *
 * WHAT IS NOT A PLACEHOLDER is `Panel` below: the fetch, the refresh, the interval, and the four
 * states. Keep it. A tile that silently shows stale data is the dashboard failure — the number
 * looks fine and is an hour old — and those four branches are what prevent it.
 */

import { useCallback, useEffect, useState } from 'react'
import { Activity, ChartPie, Layers, MessageSquareText, RefreshCw, TrendingUp } from 'lucide-react'
import type { AgentdClient } from '@agentd/client'

import './dashboard.css'
import PlaceholderActivity, { SAMPLE_ACTIVITY } from './widgets/PlaceholderActivity'
import PlaceholderAreaChart, { SAMPLE_SERIES } from './widgets/PlaceholderAreaChart'
import PlaceholderBreakdown, { SAMPLE_SLICES } from './widgets/PlaceholderBreakdown'
import PlaceholderKpi from './widgets/PlaceholderKpi'

/** How much of the grid a panel takes. `kpi` is a quarter of the top row; `half` is one of the
 *  two chart panels; `wide` spans everything. */
type PanelSize = 'kpi' | 'half' | 'wide'

/** One panel: what it is called, and how it gets its content. The fetch returns whatever your
 *  rendering understands — keep them next to each other in this file. */
interface PanelSpec {
  title: string
  /** How often to re-fetch, in seconds. 0 = only on load and the Refresh button. */
  every: number
  fetch: (client: AgentdClient) => Promise<unknown>
  render: (data: unknown) => JSX.Element
  size?: PanelSize
  /** Shown in the panel's tile. Any lucide icon. */
  icon?: JSX.Element
  /** Said under the title in mono — "every 5m", "on refresh". A number with no cadence is a
   *  number you cannot trust, because you cannot tell how old it is. */
  cadence?: string
}

/* REPLACE THESE with panels over this agent's own tools:
 *
 *   fetch: (client) => client.invokeTool('get_cost_snapshot', { period: 'today' }),
 *   render: (d: any) => <PlaceholderKpi data={{ value: `$${d.total.toFixed(2)}`, ... }} />,
 *
 * Only the first is wired to anything real (the daemon's own session list) — proof the pipe
 * works end to end. The rest render sample data and SAY SO on screen, because a placeholder that
 * looks like a measurement is worse than an empty panel.
 */
const PANELS: PanelSpec[] = [
  {
    title: 'Conversations',
    every: 0,
    size: 'kpi',
    icon: <MessageSquareText size={15} strokeWidth={1.8} />,
    cadence: 'on refresh',
    fetch: async (client) => {
      const res: any = await client.request('sessions.list', {})
      return (res?.sessions || res?.rows || []).length
    },
    render: (data) => (
      <PlaceholderKpi
        data={{ value: String(data), compare: 'held by this agent', points: undefined }}
      />
    ),
  },
  {
    title: 'A number',
    every: 0,
    size: 'kpi',
    icon: <TrendingUp size={15} strokeWidth={1.8} />,
    cadence: 'placeholder',
    fetch: async () => null,
    render: () => (
      <PlaceholderKpi
        data={{
          value: '—',
          delta: '+0%',
          direction: 'up',
          goodWhen: 'up',
          compare: 'placeholder — wire a tool',
          points: [4, 6, 5, 8, 7, 11, 10, 14],
        }}
      />
    ),
  },
  {
    title: 'Something live',
    every: 0,
    size: 'kpi',
    icon: <Activity size={15} strokeWidth={1.8} />,
    cadence: 'placeholder',
    fetch: async () => null,
    render: () => (
      <PlaceholderKpi data={{ value: '—', badge: 'live', compare: 'placeholder — wire a tool' }} />
    ),
  },
  {
    title: 'Something to watch',
    every: 0,
    size: 'kpi',
    icon: <Layers size={15} strokeWidth={1.8} />,
    cadence: 'placeholder',
    fetch: async () => null,
    render: () => (
      <PlaceholderKpi
        data={{
          value: '—',
          delta: '-0%',
          direction: 'down',
          goodWhen: 'down',
          compare: 'placeholder — wire a tool',
          points: [14, 12, 13, 9, 10, 7, 8, 5],
        }}
      />
    ),
  },
  {
    title: 'Over time',
    every: 0,
    size: 'half',
    icon: <TrendingUp size={15} strokeWidth={1.8} />,
    cadence: 'placeholder',
    fetch: async () => SAMPLE_SERIES,
    render: () => <PlaceholderAreaChart reference={32} referenceLabel="a target" />,
  },
  {
    title: 'Breakdown',
    every: 0,
    size: 'half',
    icon: <ChartPie size={15} strokeWidth={1.8} />,
    cadence: 'placeholder',
    fetch: async () => SAMPLE_SLICES,
    render: () => <PlaceholderBreakdown totalLabel="placeholder" />,
  },
  {
    title: 'What the agent did',
    every: 0,
    size: 'wide',
    icon: <Activity size={15} strokeWidth={1.8} />,
    cadence: 'placeholder',
    fetch: async () => SAMPLE_ACTIVITY,
    render: () => <PlaceholderActivity />,
  },
]

function Panel({
  spec,
  client,
  connected,
}: {
  spec: PanelSpec
  client: AgentdClient
  connected: boolean
}) {
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
    <section className={`dash-tile is-${spec.size || 'kpi'}${error ? ' is-failed' : ''}`}>
      <header className="dash-tile-head">
        {spec.icon && <span className="dash-tile-ico">{spec.icon}</span>}
        <span className="dash-tile-titles">
          <span className="dash-tile-title">{spec.title}</span>
          {spec.cadence && <span className="dash-tile-cadence">{spec.cadence}</span>}
        </span>
        <button
          className="dash-refresh"
          onClick={load}
          disabled={busy}
          title="Refresh"
          aria-label={`Refresh ${spec.title}`}
        >
          <RefreshCw size={14} strokeWidth={1.8} className={busy ? 'is-spinning' : undefined} />
        </button>
      </header>
      <div className="dash-tile-body">
        {/* THE FOUR STATES, and they are not interchangeable. "Not connected" is neutral because
            waiting is not failing — a red tile here would teach you to ignore red tiles. */}
        {!connected ? (
          <span className="dash-dim">waiting for the daemon…</span>
        ) : error ? (
          <span className="dash-error">{error}</span>
        ) : data === undefined ? (
          <span className="dash-loading">
            <span className="dash-spinner" />
            loading…
          </span>
        ) : (
          spec.render(data)
        )}
      </div>
    </section>
  )
}

export default function Dashboard({
  client,
  connected,
}: {
  client: AgentdClient
  connected: boolean
}) {
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
