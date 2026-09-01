/* The launchpad's right column — what needs you, where you were, what is left to spend.
 *
 * THREE SECTIONS, three different truth situations, handled three ways:
 *
 *   Needs you   the design surfaces build failures and risky grants here. NEITHER is recorded
 *               anywhere this window can read yet, so the section is its chrome and an honest
 *               empty line — never a sample card. When build results land somewhere readable,
 *               they land here.
 *   Recents     real, and rendered with the SAME SessionItem the sidebar uses — so the rename,
 *               the duplicate, the two-step delete and the portalled tooltip arrive for free and
 *               cannot drift from the sidebar's. Five rows: this column is a glance, and the
 *               sidebar remains the full list.
 *   Credits     the real balance, from the same hook the composer reads. The design draws a
 *               7-bar spend sparkline under it; per-day spend is not queryable, so the figure
 *               stands alone. Hidden entirely when the balance is unknown (BYOK/local) — the
 *               composer's own rule.
 *
 * Pinned bottom: the credit block is `margin-top: auto`, so it holds the design's position
 * whatever the two sections above it do.
 */

import { BellRing, CreditCard } from 'lucide-react'

import { useApp } from '../state/store'
import SessionItem from './SessionItem'

export function LaunchpadSideColumn({
  onOpenChat,
  credits,
  onCredits,
}: {
  onOpenChat: (key: string) => void
  credits: number | null
  onCredits: () => void
}) {
  const chats = useApp((s) => s.chats)
  const currentKey = useApp((s) => s.currentSessionKey)
  const recent = chats.slice(0, 5)

  return (
    <aside className="lp-side">
      <section className="lp-side-section">
        <h3 className="lp-side-label">
          <BellRing size={13} />
          Needs you
        </h3>
        {/* Honest emptiness — see the header. The line says what the section IS while there is
            nothing in it, which beats a sample card that styles itself as a fact. */}
        <p className="lp-side-empty">Nothing needs attention.</p>
      </section>

      <section className="lp-side-section">
        <h3 className="lp-side-label">Recents</h3>
        {recent.length === 0 ? (
          <p className="lp-side-empty">No conversations yet.</p>
        ) : (
          <div className="lp-recents">
            {recent.map((c) => (
              <SessionItem
                key={c.sessionId}
                session={c}
                active={c.sessionId === currentKey}
                onOpen={() => onOpenChat(c.sessionId)}
              />
            ))}
          </div>
        )}
      </section>

      {credits !== null && (
        <button
          className="lp-credit"
          onClick={onCredits}
          title="Credits & billing"
        >
          <span className="lp-credit-figure">{credits.toLocaleString()}</span>
          <span className="lp-credit-label">
            <CreditCard size={13} />
            credits
          </span>
        </button>
      )}
    </aside>
  )
}
