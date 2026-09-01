/* How full this conversation is, as a ring.
 *
 * A RING RATHER THAN A NUMBER, because the question it answers is "how much room is left", which
 * is a proportion. 47,210 means nothing without the denominator; a three-quarters-full circle
 * means something at a glance, and the exact figures are one hover away.
 *
 * It renders NOTHING until there is a real measurement. The first turn of a chat has billed no
 * tokens and an unfamiliar model has no known limit — in both cases the daemon stays silent, and
 * an empty ring drawn on a guess would be worse than no ring at all.
 */

import {
  compactTokens,
  usageTone,
  type ContextUsage,
} from '../agentd/context-usage'

const SIZE = 20
const STROKE = 2.5
const R = (SIZE - STROKE) / 2
const CIRCUMFERENCE = 2 * Math.PI * R

export function ContextRing({ usage }: { usage: ContextUsage | null }) {
  if (!usage || !usage.limit) return null

  const pct = Math.min(1, Math.max(0, usage.pct))
  const tone = usageTone(pct)
  const left = Math.max(0, usage.limit - usage.used)

  // Whole numbers in the tooltip, proportion in the ring. The cached line is there because a
  // large context is not necessarily an expensive one, and without it "180k used" reads as a
  // bill rather than as a measurement.
  const title =
    `context ${Math.round(pct * 100)}% used\n` +
    `${usage.used.toLocaleString()} of ${usage.limit.toLocaleString()} tokens ` +
    `(${compactTokens(left)} left)\n` +
    (usage.cached ? `${compactTokens(usage.cached)} served from cache\n` : '') +
    usage.model

  return (
    <span className={`ctx-ring tone-${tone}`} title={title} aria-label={title}>
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        <circle className="ctx-track" cx={SIZE / 2} cy={SIZE / 2} r={R} strokeWidth={STROKE} />
        <circle
          className="ctx-fill"
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          strokeWidth={STROKE}
          strokeDasharray={CIRCUMFERENCE}
          // Drawn from the top and clockwise: a meter that started at 3 o'clock reads as a
          // different fraction than it is.
          strokeDashoffset={CIRCUMFERENCE * (1 - pct)}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
        />
      </svg>
      {/* The percentage appears only once it MATTERS. Below the warning line the ring alone is
          enough, and a number on screen at all times is a number the eye stops reading. */}
      {tone !== 'ok' && <span className="ctx-pct">{Math.round(pct * 100)}%</span>}
    </span>
  )
}
