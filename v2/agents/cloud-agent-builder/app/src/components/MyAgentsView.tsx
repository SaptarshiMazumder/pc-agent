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

import { Download, ExternalLink, Pencil, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'

import { hasWindow, openAgentWindow } from '../agentd/app-window'
import { useClient } from '../agentd/client'
import { agentAuthorLabel, agentIsExternal, type AgentRow } from '../agentd/roster'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { useAuthorship } from '../lib/authorship'

/** One row of the registry this caller may install and has not — in practice, an agent their
 *  ORGANIZATION published. Only the handful of fields this page renders. */
type AvailableBundle = {
  id: string
  name: string
  version: string
  description: string
  publisher?: string
  compatible: boolean
  installed: boolean
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
  const { client } = useClient()
  /* WHAT THIS CALLER MAY INSTALL AND HAS NOT.
   *
   * `agents.list` answers "what is on this daemon's disk" — the shipped catalogue, this account's
   * overlay, org layers. A colleague publishing to the ORGANIZATION writes a signed bundle into
   * the org's registry, which is not a disk layer anywhere; so until somebody installs it, the
   * publish said "every member can install it now" and every member's page was unchanged.
   *
   * `marketplace.catalog` is not in the app tier — it is granted to THIS agent by name in the
   * gateway (APP_METHOD_GRANTS), so an ordinary agent's window still cannot enumerate or install
   * anything. A daemon without that grant refuses the call, and the section simply stays empty. */
  const [available, setAvailable] = useState<AvailableBundle[]>([])
  const [installing, setInstalling] = useState('')

  useEffect(() => {
    let live = true
    const onDisk = new Set(agents.map((a) => a.id))
    client
      .request<{ bundles?: AvailableBundle[] }>('marketplace.catalog', {})
      .then((d) => {
        if (!live) return
        setAvailable(
          (d.bundles || []).filter((b) => b && !b.installed && !onDisk.has(b.id)),
        )
      })
      // Silent on purpose: an older daemon refuses the method, and a red banner about a
      // capability this build never had is noise on a page that is otherwise working.
      .catch(() => {})
    return () => {
      live = false
    }
  }, [client, agents])

  async function install(bundle: AvailableBundle): Promise<void> {
    setInstalling(bundle.id)
    setError('')
    try {
      await client.request('marketplace.install', { id: bundle.id })
      setAvailable((rows) => rows.filter((r) => r.id !== bundle.id))
      onRefresh?.()
    } catch (e) {
      setError(
        `could not install ${bundle.name || bundle.id}: ${String((e as Error)?.message || e)}`,
      )
    } finally {
      setInstalling('')
    }
  }

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

        {agents.length === 0 && available.length === 0 ? (
          <div className="empty-card">
            <p>No agents yet.</p>
            <p className="row-sub">
              Start one with <strong>New agent</strong> in the sidebar — this shelf fills itself in.
            </p>
          </div>
        ) : (
          <div className="cards">
            {available.map((b) => (
              <div className="card" key={`available:${b.id}`}>
                <div className="card-top">
                  <span className="avatar lg" style={{ background: agentColor('', b.id) }}>
                    {agentInitials(b.name, b.id)}
                  </span>
                  <div>
                    <div className="card-name">
                      {b.name || b.id}
                      <span
                        className="card-tag"
                        title="Published where you can install it — not on this machine yet"
                      >
                        available
                      </span>
                    </div>
                    <div className="card-by">
                      {b.publisher ? `by ${b.publisher}` : b.id}
                      {b.version ? ` · v${b.version}` : ''}
                    </div>
                  </div>
                </div>
                <p className="card-desc">{b.description || 'No description yet.'}</p>
                <div className="card-actions">
                  <button
                    className="prime-btn"
                    disabled={installing === b.id || !b.compatible}
                    onClick={() => void install(b)}
                    title={
                      b.compatible
                        ? `Install ${b.name || b.id}`
                        : 'Built for a newer agentd than this one'
                    }
                  >
                    <Download size={15} />
                    {installing === b.id
                      ? 'Installing…'
                      : b.compatible
                        ? 'Install'
                        : 'Incompatible'}
                  </button>
                </div>
              </div>
            ))}
            {agents.map((a) => {
              const author = agentAuthorLabel(a, myId, emails)
              const external = agentIsExternal(a, enterprise, myId)
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
