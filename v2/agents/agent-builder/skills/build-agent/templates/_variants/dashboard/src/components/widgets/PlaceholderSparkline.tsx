/* PLACEHOLDER WIDGET — a sparkline. See ./README.md: reuse it, restyle it, or delete it.
 *
 * @placeholder — SCAFFOLDING, not a decision. It is here to show the look, the shape and the
 * wiring; it is not what this agent is for. Adopt it (change it, rename the file, delete this
 * tag) or delete the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * An inline SVG rather than a chart library: a dashboard tile's trend line is a dozen points and
 * a path, and a library for that is a megabyte an agent window has to download before it can draw
 * a number it already has.
 *
 * NON-TEXT COLOURS ONLY. The track is `--track` and the line is a series colour, both of which the
 * theme marks as never-for-words — nothing here has to be read, only seen.
 */

export const SAMPLE_SPARK = [12, 15, 13, 18, 17, 22, 20, 26, 24, 31, 29, 34]

export default function PlaceholderSparkline({
  points = SAMPLE_SPARK,
  stroke = 'var(--chart-1)',
  width = 96,
  height = 28,
}: {
  /** Any series. Scaled to fit — the numbers' units never matter to the drawing. */
  points?: number[]
  stroke?: string
  width?: number
  height?: number
}) {
  if (points.length < 2) return null

  const min = Math.min(...points)
  const max = Math.max(...points)
  // A FLAT SERIES IS NOT A DIVIDE-BY-ZERO. All-equal points get a mid-height line rather than NaN.
  const span = max - min || 1
  const step = width / (points.length - 1)
  const y = (v: number) => height - 2 - ((v - min) / span) * (height - 4)
  const d = points.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ')

  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="presentation"
      aria-hidden="true"
    >
      {/* The track sits behind the line at the series' own baseline, so a rising line reads as
          rising against something rather than floating. */}
      <line x1="0" y1={height - 2} x2={width} y2={height - 2} className="spark-track" />
      <path d={d} className="spark-line" style={{ stroke }} />
    </svg>
  )
}
