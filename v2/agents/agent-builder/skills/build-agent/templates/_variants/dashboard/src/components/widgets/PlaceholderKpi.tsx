/* PLACEHOLDER WIDGET — the number tile. See ./README.md: reuse it, restyle it, or delete it.
 *
 * @placeholder — SCAFFOLDING, not a decision. It is here to show the look, the shape and the
 * wiring; it is not what this agent is for. Adopt it (change it, rename the file, delete this
 * tag) or delete the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * THE SHAPE A DASHBOARD IS MADE OF: a figure, and enough context to know whether it is good news.
 * A number alone ("$412") answers nothing — up or down from what, and is that normal? So the tile
 * carries a direction, a comparison phrase, and a trend, and the delta is coloured by MEANING
 * rather than by sign: spend rising is bad, savings rising is good, and only the caller knows
 * which. Hence `goodWhen`.
 */

import { ArrowDown, ArrowUp } from 'lucide-react'

import PlaceholderSparkline, { SAMPLE_SPARK } from './PlaceholderSparkline'

export interface KpiData {
  /** The figure, already formatted — the widget never guesses at currency or precision. */
  value: string
  /** e.g. "+22%". Omit for a tile that has no comparison (see `badge`). */
  delta?: string
  /** Which way it moved. */
  direction?: 'up' | 'down'
  /** Up is good news, or down is. Spend and errors are `down`; revenue and uptime are `up`. */
  goodWhen?: 'up' | 'down'
  /** What it is being compared to — "vs last week". */
  compare?: string
  /** Instead of a delta: a standing state, like `live`. */
  badge?: string
  points?: number[]
}

export const SAMPLE_KPI: KpiData = {
  value: '—',
  delta: '+0%',
  direction: 'up',
  goodWhen: 'down',
  compare: 'no data yet — this tile is a placeholder',
  points: SAMPLE_SPARK,
}

export default function PlaceholderKpi({ data = SAMPLE_KPI }: { data?: KpiData }) {
  const { value, delta, direction, goodWhen, compare, badge, points } = data
  /* GOOD OR BAD, not up or down. A tile that paints every rise green tells you spend is going
     well. Unset `goodWhen` means the movement carries no verdict, so it stays neutral. */
  const tone = !goodWhen || !direction ? 'flat' : direction === goodWhen ? 'good' : 'bad'

  return (
    <div className="kpi">
      <span className="kpi-figure">{value}</span>
      <div className="kpi-foot">
        {badge ? (
          <span className="kpi-badge">
            <span className="live-dot is-live" />
            {badge}
          </span>
        ) : delta ? (
          <span className={`kpi-delta is-${tone}`}>
            {direction === 'down' ? (
              <ArrowDown size={13} strokeWidth={2.2} />
            ) : (
              <ArrowUp size={13} strokeWidth={2.2} />
            )}
            {delta}
          </span>
        ) : null}
        {compare && <span className="kpi-compare">{compare}</span>}
      </div>
      {points && points.length > 1 && (
        <div className="kpi-spark">
          <PlaceholderSparkline
            points={points}
            stroke={tone === 'bad' ? 'var(--danger)' : tone === 'good' ? 'var(--ok-dot)' : 'var(--chart-1)'}
          />
        </div>
      )}
    </div>
  )
}
