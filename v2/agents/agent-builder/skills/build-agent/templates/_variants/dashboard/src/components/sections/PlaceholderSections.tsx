/* PLACEHOLDER SECTIONS — three example screens. See ../widgets/README.md.
 *
 * @placeholder — SCAFFOLDING, not a decision. These exist to show what a section IS: a rail row
 * that opens a whole screen with its own head, its own panels and its own table. They are not
 * this agent's screens. Adopt one (change it, rename the file, delete this tag) or delete the
 * file and the one line in App.tsx that spreads it in. `validate_agent` refuses to pack or
 * publish while the tag remains.
 *
 * DO NOT BUILD THE AGENT AROUND THESE. Decide what screens the agent needs, then keep whichever
 * of these is closest and delete the rest. An agent with one screen should ship with one screen —
 * three sections because three were here is a window shaped like a template instead of like its
 * job.
 *
 * WHAT EACH ONE DEMONSTRATES, which is the reason there are three rather than one:
 *
 *   Items    a TABLE screen — the list behind a number, with tabs, filter and paging.
 *   Trends   a CHART screen — a series over time beside its breakdown.
 *   Alerts   a QUEUE screen — things needing attention, and a count on the rail row.
 */

import { Activity, ChartPie, Layers, TriangleAlert, TrendingUp } from 'lucide-react'

import PlaceholderActivity from '../widgets/PlaceholderActivity'
import PlaceholderAreaChart from '../widgets/PlaceholderAreaChart'
import PlaceholderBreakdown from '../widgets/PlaceholderBreakdown'
import PlaceholderKpi from '../widgets/PlaceholderKpi'
import PlaceholderTable from '../widgets/PlaceholderTable'
import type { SectionSpec } from './section'

/** A panel, without the fetch machinery — these screens show sample data, so wiring them to
 *  `PanelSpec` would be pretending. The real one is `Panel` in ../Dashboard.tsx. */
function Tile({
  title,
  icon,
  size = 'kpi',
  children,
}: {
  title: string
  icon: JSX.Element
  size?: 'kpi' | 'half' | 'wide'
  children: React.ReactNode
}) {
  return (
    <section className={`dash-tile is-${size}`}>
      <header className="dash-tile-head">
        <span className="dash-tile-ico">{icon}</span>
        <span className="dash-tile-titles">
          <span className="dash-tile-title">{title}</span>
          <span className="dash-tile-cadence">placeholder</span>
        </span>
      </header>
      <div className="dash-tile-body">{children}</div>
    </section>
  )
}

const KPI = (label: string, value: string, compare: string) => (
  <Tile key={label} title={label} icon={<Layers size={15} strokeWidth={1.8} />}>
    <PlaceholderKpi data={{ value, compare }} />
  </Tile>
)

export const PLACEHOLDER_SECTIONS: SectionSpec[] = [
  {
    id: 'items',
    label: 'Items',
    icon: <Layers size={16} strokeWidth={1.8} />,
    headline: 'The list behind a number',
    blurb:
      'A section is a whole screen, not a tab: its own head, its own panels, its own table. ' +
      'This one is a placeholder — replace it with the list your agent actually keeps.',
    render: () => (
      <div className="dashboard-grid">
        {KPI('Total', '—', 'placeholder')}
        {KPI('Open', '—', 'placeholder')}
        {KPI('Closed', '—', 'placeholder')}
        {KPI('This week', '—', 'placeholder')}
        <Tile title="Everything" icon={<Layers size={15} strokeWidth={1.8} />} size="wide">
          <PlaceholderTable />
        </Tile>
      </div>
    ),
  },
  {
    id: 'trends',
    label: 'Trends',
    icon: <TrendingUp size={16} strokeWidth={1.8} />,
    headline: 'How it moved, and what it is made of',
    blurb:
      'A series beside its breakdown answers two different questions — when did this change, ' +
      'and what is driving it. Placeholder: point both at your agent’s own figures.',
    render: () => (
      <div className="dashboard-grid">
        <Tile title="Over time" icon={<TrendingUp size={15} strokeWidth={1.8} />} size="half">
          <PlaceholderAreaChart reference={32} referenceLabel="a target" />
        </Tile>
        <Tile title="Breakdown" icon={<ChartPie size={15} strokeWidth={1.8} />} size="half">
          <PlaceholderBreakdown totalLabel="placeholder" />
        </Tile>
      </div>
    ),
  },
  {
    id: 'alerts',
    label: 'Alerts',
    icon: <TriangleAlert size={16} strokeWidth={1.8} />,
    /* THE COUNT IS ON THE RAIL ROW because a queue nobody opens is a queue nobody empties. Only
       show one when the number is real — this is sample data, hence the placeholder cadence. */
    count: '2',
    headline: 'What needs a person',
    blurb:
      'The queue an agent cannot clear on its own. Placeholder: fill it from whatever your ' +
      'agent flags, and delete the section entirely if it never needs anyone.',
    render: () => (
      <div className="dashboard-grid">
        <Tile title="Needs attention" icon={<Activity size={15} strokeWidth={1.8} />} size="wide">
          <PlaceholderActivity />
        </Tile>
      </div>
    ),
  },
]
