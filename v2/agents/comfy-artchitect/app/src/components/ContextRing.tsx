/* ContextRing — how full the conversation's context window is, as a ring.
 *
 * WHY A RING AND NOT "4% context". The words were three things at once: a number, a unit and a
 * noun, sat in a row of chips that are each one thing. On a narrow composer "4% context" wrapped
 * onto two lines and became the tallest item in the row — a status nobody reads on most turns
 * making itself the biggest thing next to the send button.
 *
 * A ring is read WITHOUT being read. Nearly empty for most of a conversation, obviously filling
 * near the end, and the exact figure is one hover away for the moment it starts to matter. That
 * is the right weight for this: it is a warning that becomes relevant late, not a reading anyone
 * needs on turn two.
 *
 * NO LIBRARY AND NO TEXT INSIDE IT. Two circles and a dash offset; a percentage rendered inside
 * a 16px ring is unreadable anyway, which is what the tooltip is for.
 */

const SIZE = 16
const STROKE = 2.5
const R = (SIZE - STROKE) / 2
const CIRCUMFERENCE = 2 * Math.PI * R

/** Past this, the conversation is close enough to its limit to say so in colour. */
const WARN_PCT = 75
const FULL_PCT = 90

export function ContextRing({
  pct,
  used,
  limit,
}: {
  /** 0–100, already rounded by the caller. */
  pct: number
  used: number
  limit: number
}): JSX.Element {
  // CLAMPED, because a model that reports more used than its limit (it happens — a limit is a
  // configured number, not a measured one) would otherwise draw an arc longer than the circle
  // and read as nearly empty.
  const safe = Math.max(0, Math.min(100, pct))
  const state = safe >= FULL_PCT ? 'full' : safe >= WARN_PCT ? 'warn' : ''

  return (
    <span
      className={`ctx-ring ${state}`}
      title={`Context ${safe}% full — ${used.toLocaleString()} of ${limit.toLocaleString()} tokens`}
      role="img"
      aria-label={`Context ${safe} percent full`}
    >
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        {/* The track. Always a full circle, so the ring reads as a gauge even at 0%. */}
        <circle
          className="ctx-ring-track"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          fill="none"
          strokeWidth={STROKE}
        />
        {/* The fill. Rotated so it starts at twelve o'clock rather than at three. */}
        <circle
          className="ctx-ring-fill"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - safe / 100)}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
        />
      </svg>
    </span>
  )
}

export default ContextRing
