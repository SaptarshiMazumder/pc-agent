/* The topbar holds only what is about THIS VIEW. Actions live in the rail.
 *
 * The crumb names what the conversation is working on. The chat is always WITH Agent Builder, so
 * "· building X" rather than "/ X", which read like a breadcrumb into someone else's thread.
 */

import type { WhoAmI } from '../agentd/platform'

export function Topbar({
  root,
  leaf,
  who,
  canTogglePanel,
  onTogglePanel,
}: {
  root: string
  leaf: string
  who: WhoAmI
  canTogglePanel: boolean
  onTogglePanel: () => void
}) {
  return (
    <header className="topbar glass">
      <div className="crumbs">
        <span className="crumb-root">{root}</span>
        {leaf && <span className="sep">·</span>}
        <span className="crumb-leaf" title="the agent shown in the inspector">
          {leaf}
        </span>
      </div>

      <div className="topbar-actions">
        {/* WHO this window is connected as — the identity a Publish would be signed with. Hidden
            entirely when the daemon is too old to answer; "not signed in" only when it SAID so. */}
        {who.known && (
          <span className={`whoami ${who.signedIn ? '' : 'anon'}`} title={who.title}>
            {who.label}
          </span>
        )}
        {canTogglePanel && (
          <button className="icon-btn" onClick={onTogglePanel} title="Toggle inspector">
            ▤
          </button>
        )}
      </div>
    </header>
  )
}
