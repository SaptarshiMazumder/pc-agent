/* PLACEHOLDER WIDGET — one figure about this run. See ./README.md.
 *
 * @placeholder — SCAFFOLDING, not a decision. It shows the look, the shape and the wiring; it is
 * not what this agent is for. Adopt it (change it, rename the file, delete this tag) or delete
 * the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * The aside's shape: a quiet label, one number, and at most one thing under it. The two REAL
 * cards beside it (context used, credits) are built from this same markup in App.tsx — so
 * adopting this widget means giving it a figure the agent actually knows.
 *
 * NEVER INVENT THE NUMBER. An aside of plausible figures is the most confidently wrong thing a
 * window can show: nobody checks a number that looks calm. If the agent cannot measure it, the
 * card should not exist.
 */

import type { ReactNode } from 'react'

export default function PlaceholderStat({
  icon,
  label,
  value,
  sub,
  /** 0-100. Draws the bar; omit for a card that is just a figure. */
  pct,
}: {
  icon: ReactNode
  label: string
  value: string
  sub?: string
  pct?: number
}) {
  return (
    <div className="stat-card">
      <span className="stat-head">
        {icon}
        {label}
      </span>
      <span className="stat-figure">{value}</span>
      {pct !== undefined && (
        <span className="stat-bar">
          {/* The VALUE travels as a custom property, not as a width: a percentage is data, and how
              it is drawn stays in the stylesheet where a theme can reach it. */}
          <span className="stat-bar-fill" style={{ '--pct': pct } as React.CSSProperties} />
        </span>
      )}
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  )
}
