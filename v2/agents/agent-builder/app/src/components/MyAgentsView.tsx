/* My Agents — the shelf of everything this machine has built, as cards.
 *
 * THE SAME PAGE agentd shows, minus the half this window is not allowed to serve. agentd's version
 * also carries a "From the platform" grid backed by `marketplace.catalog` and an Add button backed
 * by `marketplace.install`. Both are HOST-ONLY methods: an app connection asking for them is
 * refused by the daemon, so rendering them here would produce a grid that never loads and a button
 * that always errors. They are absent rather than broken.
 *
 * WHAT IT DOES DO is the pair of verbs this window exists for:
 *
 *   Open   the agent's own window, through `openAgentWindow` — the SAME mechanism as the button
 *          beside the composer. One implementation: it resolves the launch url, and it is the one
 *          place that knows an agent with no working `[app]` cannot be opened at all.
 *   Edit   point the conversation at that agent, through the same `editAgent` the picker uses:
 *          reset the thread, select it in the inspector, tell the model what it is looking at.
 *
 * OPEN IS ABSENT, NOT DISABLED, when an agent has no window. The daemon only fills in `app.url`
 * for an agent that declares `[app]` AND whose entry file is really on disk, so its absence means
 * there is nothing to open — and nothing the user could click to change that. A greyed button
 * invites a click that can never work; no button says "this one is chat-only", which is true.
 */

import { ExternalLink, Pencil, RefreshCw } from 'lucide-react'
import { useState } from 'react'

import { hasWindow, openAgentWindow } from '../agentd/app-window'
import type { AgentRow } from '../agentd/roster'
import { agentColor, agentInitials } from '../lib/agentPresentation'

/** THE ONE Open. Every surface that offers "open this agent's window" — the cards below, the
 *  launchpad's table, whatever comes after them — goes through this hook, so the busy state, the
 *  error wording and the mechanism itself cannot drift between two copies of the same button.
 *
 *  The error is a VALUE to render, not a toast: "I clicked Open and nothing happened" is the one
 *  failure a console message cannot answer, because nobody has the console open. */
export function useOpenAgent(): {
  opening: string
  error: string
  open: (agent: AgentRow) => Promise<void>
} {
  const [opening, setOpening] = useState('')
  const [error, setError] = useState('')

  async function open(agent: AgentRow): Promise<void> {
    setOpening(agent.id)
    setError('')
    try {
      await openAgentWindow(agent)
    } catch (e) {
      setError(`could not open ${agent.name || agent.id}: ${String((e as Error)?.message || e)}`)
    } finally {
      setOpening('')
    }
  }

  return { opening, error, open }
}

/** The shelf ITSELF — the cards, their two verbs, and the failure they can produce. Split out of
 *  the page so the launchpad could show the same shelf under its own heading while its table was
 *  being built; the old My-Agents page still draws it. */
export function AgentShelf({
  agents,
  onEdit,
}: {
  agents: AgentRow[]
  onEdit: (id: string) => void
}) {
  const { opening, error, open } = useOpenAgent()

  return (
    <>
      {error && <div className="page-error">{error}</div>}

      {agents.length === 0 ? (
        <div className="empty-card">
          <p>No agents yet.</p>
          <p className="row-sub">
            Start one with <strong>New agent</strong> in the sidebar — this shelf fills itself in.
          </p>
        </div>
      ) : (
        <div className="cards">
          {agents.map((a) => (
            <div className="card" key={a.id}>
              <div className="card-top">
                <span className="avatar lg" style={{ background: agentColor(a.color, a.id) }}>
                  {agentInitials(a.name, a.id)}
                </span>
                <div>
                  <div className="card-name">{a.name || a.id}</div>
                  <div className="card-by">
                    {a.id}
                    {a.version ? ` · v${a.version}` : ''}
                  </div>
                </div>
              </div>
              <p className="card-desc">{a.description || a.tagline || 'No description yet.'}</p>
              <div className="card-actions">
                <button className="ghost-btn" onClick={() => onEdit(a.id)} title="Work on this agent">
                  <Pencil size={15} />
                  Edit
                </button>
                {hasWindow(a) && (
                  <button
                    className="prime-btn"
                    disabled={opening === a.id}
                    onClick={() => void open(a)}
                    title={`Open ${a.app?.title || a.name || a.id}`}
                  >
                    <ExternalLink size={15} />
                    {opening === a.id ? 'Opening…' : 'Open'}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

/** The PAGE — the frame and the head, with the shelf inside it. What it renders is unchanged; the
 *  cards simply live in AgentShelf now. Kept while the launchpad grows into its replacement. */
export function MyAgentsView({
  agents,
  onEdit,
  onRefresh,
}: {
  agents: AgentRow[]
  onEdit: (id: string) => void
  onRefresh?: () => void
}) {
  return (
    <div className="page-scroll">
      <div className="page-inner">
        <header className="page-head">
          <div>
            <h1>My Agents</h1>
            <p>Everything built on this machine. Open one to use it, or edit one to keep working.</p>
          </div>
          {onRefresh && (
            <button className="ghost-btn" onClick={onRefresh} title="Re-read the roster">
              <RefreshCw size={15} />
              Refresh
            </button>
          )}
        </header>

        <AgentShelf agents={agents} onEdit={onEdit} />
      </div>
    </div>
  )
}
