/* PLACEHOLDER WIDGET — a series over time. See ./README.md: reuse it, restyle it, or delete it.
 *
 * @placeholder — SCAFFOLDING, not a decision. It is here to show the look, the shape and the
 * wiring; it is not what this agent is for. Adopt it (change it, rename the file, delete this
 * tag) or delete the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * Inline SVG, same argument as the sparkline: a filled line and a few gridlines do not justify a
 * charting dependency in a window that has to download itself.
 *
 * IT DRAWS A REFERENCE LINE. Almost every dashboard series is only meaningful against something —
 * a budget, a target, last month — so the reference is part of the widget rather than an extra
 * somebody remembers to add. Pass none and it is simply not drawn.
 */

export interface SeriesPoint {
  label: string
  value: number
}

export const SAMPLE_SERIES: SeriesPoint[] = [
  { label: 'Mon', value: 18 },
  { label: 'Tue', value: 24 },
  { label: 'Wed', value: 21 },
  { label: 'Thu', value: 30 },
  { label: 'Fri', value: 27 },
  { label: 'Sat', value: 36 },
  { label: 'Sun', value: 33 },
]

export default function PlaceholderAreaChart({
  series = SAMPLE_SERIES,
  reference,
  referenceLabel = 'target',
  height = 168,
}: {
  series?: SeriesPoint[]
  /** A budget, a target, a threshold — drawn as a dashed rule across the plot. */
  reference?: number
  referenceLabel?: string
  height?: number
}) {
  if (series.length < 2) return null

  const W = 560
  const H = height
  const PAD_B = 22 // room for the labels under the plot
  const values = series.map((p) => p.value)
  const max = Math.max(...values, reference ?? 0) * 1.15 || 1
  const step = W / (series.length - 1)
  const y = (v: number) => (H - PAD_B) - (v / max) * (H - PAD_B - 6)

  const line = series
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${y(p.value).toFixed(1)}`)
    .join(' ')
  const area = `${line} L${W},${H - PAD_B} L0,${H - PAD_B} Z`

  return (
    <div className="chart">
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="presentation">
        <defs>
          <linearGradient id="ph-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity="0.26" />
            <stop offset="100%" stopColor="var(--chart-1)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Gridlines first, so the series sits on top of them. */}
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} className="chart-grid" x1="0" x2={W} y1={y(max * f)} y2={y(max * f)} />
        ))}

        <path className="chart-area" d={area} fill="url(#ph-area)" />
        <path className="chart-line" d={line} />

        {reference !== undefined && (
          <line className="chart-ref" x1="0" x2={W} y1={y(reference)} y2={y(reference)} />
        )}
      </svg>

      {/* The labels are HTML, not SVG text: the chart stretches with `preserveAspectRatio="none"`
          and any text inside it would stretch with it. */}
      <div className="chart-axis">
        {series.map((p) => (
          <span key={p.label}>{p.label}</span>
        ))}
      </div>
      {reference !== undefined && <span className="chart-ref-label">{referenceLabel}</span>}
    </div>
  )
}
