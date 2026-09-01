/* PLACEHOLDER WIDGET — a donut and its legend. See ./README.md: reuse, restyle or delete.
 *
 * @placeholder — SCAFFOLDING, not a decision. It is here to show the look, the shape and the
 * wiring; it is not what this agent is for. Adopt it (change it, rename the file, delete this
 * tag) or delete the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * "What is the total made of." Drawn with stroke-dasharray on circles rather than arc paths: the
 * maths is one subtraction per slice instead of trigonometry, and a donut is the only shape it
 * will ever need to be.
 *
 * THE LEGEND CARRIES THE NUMBERS. A donut alone is a shape you squint at — every real question
 * ("how much was storage?") is answered by the rows beside it, which is why they are part of the
 * widget rather than an optional extra.
 */

export interface Slice {
  label: string
  value: number
  color?: string
}

export const SAMPLE_SLICES: Slice[] = [
  { label: 'First group', value: 42 },
  { label: 'Second group', value: 28 },
  { label: 'Third group', value: 18 },
  { label: 'Everything else', value: 12 },
]

const SERIES = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)']

export default function PlaceholderBreakdown({
  slices = SAMPLE_SLICES,
  total,
  totalLabel = 'total',
  format = (v: number) => String(v),
}: {
  slices?: Slice[]
  /** The figure in the middle. Defaults to the sum — pass one only if it differs. */
  total?: string
  totalLabel?: string
  format?: (v: number) => string
}) {
  const sum = slices.reduce((a, s) => a + s.value, 0) || 1
  const R = 54
  const C = 2 * Math.PI * R

  let offset = 0
  const arcs = slices.map((s, i) => {
    const len = (s.value / sum) * C
    const arc = { ...s, len, offset, color: s.color || SERIES[i % SERIES.length] }
    offset += len
    return arc
  })

  return (
    <div className="donut-wrap">
      <div className="donut">
        <svg viewBox="0 0 140 140" role="presentation" aria-hidden="true">
          <circle className="donut-track" cx="70" cy="70" r={R} />
          {arcs.map((a) => (
            <circle
              key={a.label}
              className="donut-arc"
              cx="70"
              cy="70"
              r={R}
              stroke={a.color}
              strokeDasharray={`${a.len} ${C - a.len}`}
              strokeDashoffset={-a.offset}
            />
          ))}
        </svg>
        <div className="donut-centre">
          <span className="donut-total">{total ?? format(sum)}</span>
          <span className="donut-total-label">{totalLabel}</span>
        </div>
      </div>

      <ul className="donut-legend">
        {arcs.map((a) => (
          <li key={a.label}>
            <span className="donut-swatch" style={{ background: a.color }} />
            <span className="donut-label">{a.label}</span>
            <span className="donut-value">{format(a.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
