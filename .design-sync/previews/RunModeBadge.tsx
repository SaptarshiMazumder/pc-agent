/* RunModeBadge — renders nothing until a live daemon answers its auth read, so there is no
 * static state to show. The cell says so instead of showing an empty strip; the component is
 * still mounted (really rendered, really null) so a future static state would appear here. */
import { RunModeBadge } from 'agent-app'

export const NeedsLiveDaemon = () => (
  <div style={{ padding: 'var(--sp-control)', color: 'var(--faint)' }}>
    <RunModeBadge />
    <div style={{ fontSize: 'var(--fs-meta)', lineHeight: 'var(--lh-body)' }}>
      RunModeBadge draws the local/cloud run-mode pill from a live daemon connection — it
      deliberately renders nothing until that first read answers, so a static preview has no
      state to show. Wire it with <code style={{ fontFamily: 'var(--mono)' }}>client</code> from
      the window's connection.
    </div>
  </div>
)
