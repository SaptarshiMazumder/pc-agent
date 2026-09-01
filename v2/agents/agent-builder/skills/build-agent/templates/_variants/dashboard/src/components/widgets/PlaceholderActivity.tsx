/* PLACEHOLDER WIDGET — what the agent did. See ./README.md: reuse, restyle or delete.
 *
 * @placeholder — SCAFFOLDING, not a decision. It is here to show the look, the shape and the
 * wiring; it is not what this agent is for. Adopt it (change it, rename the file, delete this
 * tag) or delete the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * NEWEST FIRST, and every row says what happened, to what, and whether it needs you. A dashboard
 * that only shows numbers cannot answer "why did that change" — this is where the answer lives,
 * and it is the one panel worth keeping even in an agent with no charts at all.
 *
 * THE STATUS IS A PILL WITH A MEANING, not a colour: `needs-you` is the only one that asks for
 * anything, so it is the only warm one. Done and logged are quiet on purpose — a list where every
 * row shouts is a list nobody reads.
 */

import { Check, ChevronRight, Clock, TriangleAlert } from 'lucide-react'

export type ActivityStatus = 'needs-you' | 'done' | 'logged'

export interface ActivityRow {
  id: string
  /** What the agent did — "Rotated the access key". */
  action: string
  /** What it did it to — a service, a file, an account. Rendered in mono. */
  target?: string
  /** The consequence, if there is a number to give: "-$18/day", "3 files". */
  impact?: string
  /** Good news, bad news, or neither — colours `impact` only. */
  impactTone?: 'good' | 'bad'
  status: ActivityStatus
  when?: string
}

export const SAMPLE_ACTIVITY: ActivityRow[] = [
  {
    id: 'a1',
    action: 'This panel is a placeholder',
    target: 'widgets/PlaceholderActivity.tsx',
    impact: 'no data',
    status: 'logged',
    when: 'just now',
  },
  {
    id: 'a2',
    action: 'Replace it with your agent’s own activity',
    target: 'components/Dashboard.tsx',
    status: 'needs-you',
    when: 'whenever',
  },
]

const ICON: Record<ActivityStatus, JSX.Element> = {
  'needs-you': <TriangleAlert size={14} strokeWidth={1.8} />,
  done: <Check size={14} strokeWidth={2.2} />,
  logged: <Clock size={14} strokeWidth={1.8} />,
}

const LABEL: Record<ActivityStatus, string> = {
  'needs-you': 'Needs you',
  done: 'Done',
  logged: 'Logged',
}

export default function PlaceholderActivity({
  rows = SAMPLE_ACTIVITY,
  onOpen,
}: {
  rows?: ActivityRow[]
  /** Optional. Without it the rows are not interactive — a chevron that does nothing is worse
   *  than no chevron, so it is only drawn when there is somewhere to go. */
  onOpen?: (row: ActivityRow) => void
}) {
  if (!rows.length) return <p className="dash-dim">nothing yet</p>

  return (
    <ul className="activity">
      {rows.map((r) => {
        const Row = onOpen ? 'button' : 'div'
        return (
          <li key={r.id}>
            <Row
              className={`activity-row${onOpen ? ' is-clickable' : ''}`}
              {...(onOpen ? { onClick: () => onOpen(r), type: 'button' as const } : {})}
            >
              <span className={`activity-ico is-${r.status}`}>{ICON[r.status]}</span>
              <span className="activity-text">
                <span className="activity-action">{r.action}</span>
                {r.target && <span className="activity-target">{r.target}</span>}
              </span>
              {r.impact && (
                <span className={`activity-impact${r.impactTone ? ` is-${r.impactTone}` : ''}`}>
                  {r.impact}
                </span>
              )}
              <span className={`activity-pill is-${r.status}`}>{LABEL[r.status]}</span>
              {r.when && <span className="activity-when">{r.when}</span>}
              {onOpen && <ChevronRight className="activity-go" size={15} strokeWidth={1.8} />}
            </Row>
          </li>
        )
      })}
    </ul>
  )
}
