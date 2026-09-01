/* The Launchpad — where a session starts.
 *
 * WHAT IT REPLACES, and why it is not just the shelf renamed. `MyAgentsView` answered one
 * question ("what have I built?") and the empty chat answered another ("what shall we build?"),
 * and they were different screens, so beginning anything meant knowing which one to be on. This
 * page is both: pick a shape to start from, see everything you own, and deal with what is broken.
 *
 * THE FRAME IS THE POINT OF THIS FIRST PASS. The page owns its own scroll — one region scrolls
 * and everything else is `flex:none` — for the reason PageShell gives: pinning with
 * `position:sticky` instead would suppress the app-wide soft scroll-edge fade on the body. This
 * is not PageShell itself because the design puts a full-width bar above TWO columns, which is a
 * different skeleton, not a different set of props.
 *
 * The sections arrive one at a time. Today the shelf is the same cards `MyAgentsView` draws —
 * the SAME component, so Open and Edit cannot drift while the table that will replace them is
 * being built.
 */

import { CreditCard } from 'lucide-react'
import { useState } from 'react'

import type { AgentRow } from '../agentd/roster'
import { useApp } from '../state/store'
import { LaunchpadAgentTable } from './LaunchpadAgentTable'
import { LaunchpadSideColumn } from './LaunchpadSideColumn'
import { LaunchpadTemplateShelf } from './LaunchpadTemplateShelf'
import SearchBox from './SearchBox'

export function Launchpad({
  agents,
  onEdit,
  onCreate,
  onCreateShape,
  onPreviewTemplate,
  onOpenChat,
  credits,
  onCredits,
  status,
  daemonVersion,
}: {
  agents: AgentRow[]
  onEdit: (id: string) => void
  /** Opens the create flow (the Blueprint) with nothing pre-answered — the dashed tile's door. */
  onCreate: () => void
  /** The create flow with a Shape already picked — the Headless card, and the viewer's Use. */
  onCreateShape: (shape: string) => void
  /** Full-screen template walkthrough for a windowed template's card or eye badge. */
  onPreviewTemplate: (id: string) => void
  /** Resume a saved conversation — the same door the sidebar's Recents use. */
  onOpenChat: (key: string) => void
  /** The live balance, or null when unknown — null hides the block (the composer's rule). */
  credits: number | null
  onCredits: () => void
  /** Socket state + daemon version — the same two facts the sidebar's live chip reads. */
  status: string
  daemonVersion: string
}) {
  /* The refresh the old page offered but never wired (App passed no onRefresh) — the store
     already knows how to re-read the roster, so the button is real from day one. */
  const reloadAgents = useApp((s) => s.reloadAgents)

  /* THE TOP-BAR SEARCH IS SCOPED TO THIS PAGE — it filters the table below, live, with the
     sidebar's own predicate (name | id | tagline), so the two searches agree about what matches.
     It is not a global search, because no such thing exists yet; a field that pretended to be
     one would be a dead control wearing a shortcut chip. */
  const [q, setQ] = useState('')
  const needle = q.trim().toLowerCase()
  const shown = agents.filter(
    (a) => !needle || `${a.name || ''} ${a.id} ${a.tagline || ''}`.toLowerCase().includes(needle),
  )

  return (
    <div className="lp">
      <div className="lp-topbar">
        {/* The same two facts as the sidebar's chip, restated where the design puts them — the
            sidebar may be collapsed to icons, and this page is where you land after a restart. */}
        <span className="lp-live" title={`${status}${daemonVersion ? ` · agentd ${daemonVersion}` : ''}`}>
          <span className="live-dot" style={{ opacity: status === 'open' ? 1 : 0.3 }} />
          {status === 'open' ? `agentd ${daemonVersion}`.trim() : status === 'closed' ? 'down' : '…'}
        </span>
        <SearchBox
          className="lp-search"
          value={q}
          onChange={setQ}
          placeholder="Search your agents"
        />
        {credits !== null && (
          <button className="lp-topbar-credits" onClick={onCredits} title="Credits & billing">
            <CreditCard size={15} />
            {credits.toLocaleString()}
          </button>
        )}
      </div>

      <div className="lp-body">
        <div className="lp-main">
          <h2 className="lp-title">Start something</h2>

          <LaunchpadTemplateShelf
            onDescribe={onCreate}
            onHeadless={() => onCreateShape('headless')}
            onPreview={onPreviewTemplate}
          />

          <LaunchpadAgentTable
            agents={shown}
            searching={!!needle}
            onEdit={onEdit}
            onRefresh={() => void reloadAgents()}
          />
        </div>

        <LaunchpadSideColumn onOpenChat={onOpenChat} credits={credits} onCredits={onCredits} />
      </div>
    </div>
  )
}
