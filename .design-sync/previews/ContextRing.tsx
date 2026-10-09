/* ContextRing — the composer's 16px gauge of how full the conversation's context window is.
 * No text inside it by design; the exact figure lives in its hover tooltip (not capturable
 * statically). The cells sweep the fill axis: accent while there is room, warn from 75%,
 * danger from 90%, clamped at 100% when a model reports more used than its limit.
 *
 * `FillLevels` shows the ring at its real size beside a caption naming each level (the caption
 * is the preview's, not the component's). `Magnified` is the same sweep at 4x, so the arc,
 * the twelve-o'clock start and the round cap can be read. */
import { ContextRing } from 'agent-app'

const LIMIT = 200_000
const LEVELS = [
  { pct: 4, note: 'turn two — nearly empty' },
  { pct: 46, note: 'mid-conversation' },
  { pct: 78, note: 'warn (≥ 75%)' },
  { pct: 94, note: 'near the limit (≥ 90%)' },
  { pct: 112, note: 'over-reported — clamped to 100%' },
]
const used = (pct: number) => Math.round((LIMIT * pct) / 100)

export const FillLevels = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 560 }}>
    {LEVELS.map((l) => (
      <div key={l.pct} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ContextRing pct={l.pct} used={used(l.pct)} limit={LIMIT} />
        <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--text)', width: 44 }}>{Math.min(100, l.pct)}%</span>
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>
          {used(Math.min(100, l.pct)).toLocaleString()} of {LIMIT.toLocaleString()} tokens · {l.note}
        </span>
      </div>
    ))}
  </div>
)

export const Magnified = () => (
  <div style={{ display: 'flex', gap: 28, alignItems: 'center', zoom: 4 } as React.CSSProperties}>
    {LEVELS.slice(0, 4).map((l) => (
      <ContextRing key={l.pct} pct={l.pct} used={used(l.pct)} limit={LIMIT} />
    ))}
  </div>
)
