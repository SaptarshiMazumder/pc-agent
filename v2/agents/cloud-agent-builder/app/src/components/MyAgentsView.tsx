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
import { useEffect, useState } from 'react'

import { hasWindow, openAgentWindow } from '../agentd/app-window'
import { agentAuthorLabel, agentIsExternal, type AgentRow } from '../agentd/roster'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { useAuthSession } from '../lib/auth'
import { fetchMyOrgs, fetchOrgDetail } from '../lib/orgs'

/** The org context the row labels need: is the caller a team (enterprise), who are they, and the
 *  best-effort author id→email map (org detail names members for an admin; a plain member gets
 *  none and the byline falls back to the id). Self-contained so the shelf stays a drop-in. */
function useAuthorship(): { enterprise: boolean; myId: string; emails: Record<string, string> } {
  const session = useAuthSession()
  const [state, setState] = useState<{
    enterprise: boolean
    myId: string
    emails: Record<string, string>
  }>({ enterprise: false, myId: '', emails: {} })
  useEffect(() => {
    if (!session) {
      setState({ enterprise: false, myId: '', emails: {} })
      return
    }
    let live = true
    fetchMyOrgs()
      .then(async (d) => {
        const map: Record<string, string> = {}
        await Promise.all(
          d.orgs.map((o) =>
            fetchOrgDetail(o.id)
              .then((det) => {
                for (const m of det.members || []) if (m.accountId) map[m.accountId] = m.email || ''
              })
              .catch(() => {}),
          ),
        )
        if (live) setState({ enterprise: d.orgs.length > 0, myId: session.accountId || '', emails: map })
      })
      .catch(() => {
        if (live) setState({ enterprise: false, myId: session.accountId || '', emails: {} })
      })
    return () => {
      live = false
    }
  }, [session])
  return state
}

export function MyAgentsView({
  agents,
  onEdit,
  onRefresh,
}: {
  agents: AgentRow[]
  onEdit: (id: string) => void
  onRefresh?: () => void
}) {
  const [opening, setOpening] = useState('')
  const [error, setError] = useState('')
  const { enterprise, myId, emails } = useAuthorship()

  async function open(agent: AgentRow): Promise<void> {
    setOpening(agent.id)
    setError('')
    try {
      await openAgentWindow(agent)
    } catch (e) {
      // Surfaced on the page, not swallowed: "I clicked Open and nothing happened" is the one
      // failure a console message cannot answer, because nobody has the console open.
      setError(`could not open ${agent.name || agent.id}: ${String((e as Error)?.message || e)}`)
    } finally {
      setOpening('')
    }
  }

  return (
    <div className="page-scroll">
      <div className="page-inner">
        <header className="page-head">
          <div>
            <h1>Agents</h1>
            <p>
              {enterprise
                ? 'Every agent you can use — your own and your organization’s. Each card names its author.'
                : 'Every agent you can use — the ones you built or installed.'}
            </p>
          </div>
          {onRefresh && (
            <button className="ghost-btn" onClick={onRefresh} title="Re-read the roster">
              <RefreshCw size={15} />
              Refresh
            </button>
          )}
        </header>

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
            {agents.map((a) => {
              const author = agentAuthorLabel(a, myId, emails)
              const external = agentIsExternal(a, enterprise)
              return (
              <div className="card" key={a.id}>
                <div className="card-top">
                  <span className="avatar lg" style={{ background: agentColor(a.color, a.id) }}>
                    {agentInitials(a.name, a.id)}
                  </span>
                  <div>
                    <div className="card-name">
                      {a.name || a.id}
                      {a.scope === 'org' && (
                        <span className="card-tag" title="Your organization's — everyone in it can use it">
                          org
                        </span>
                      )}
                      {external && (
                        <span className="card-tag" title="From outside your world — an installed copy, or (in a team) not your organization's">
                          external
                        </span>
                      )}
                    </div>
                    <div className="card-by">
                      {author ? `by ${author}` : a.id}
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
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
