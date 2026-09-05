/* My Agents — the shelf of everything this machine has built, PLUS everything this account may
 * install and has not, as cards.
 *
 * THE MARKETPLACE HALF USED TO BE ABSENT HERE, and the reason it gave was true when it was
 * written: `marketplace.catalog` and `marketplace.install` were host-only, an app connection
 * asking for them was refused, and rendering a grid that never loads is worse than rendering
 * nothing. That premise is gone. The daemon now grants those three methods to THIS agent by name
 * (APP_METHOD_GRANTS in gateway.py), exactly so this window can show a colleague's publish — an
 * ordinary agent's window still cannot enumerate or install anything.
 *
 * WHY IT MATTERS HERE and not only in the shell: an enterprise publish writes a signed bundle
 * into the ORG'S registry, which is not a disk layer on anybody's machine. `agents.list` answers
 * "what is on this disk", so until somebody installs it, the publish said "every member can
 * install it now" and every member's builder was unchanged — including the publisher's own, since
 * publishing does not install. A card that says `available` is the whole difference.
 *
 * A DAEMON WITHOUT THE GRANT refuses the call and the section simply stays empty: an older build
 * shows exactly what it showed before, rather than an error about a capability it never had.
 *
 * THE OTHER TWO VERBS, unchanged:
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
import type { AgentRow } from '../agentd/roster'
import { agentColor, agentInitials } from '../lib/agentPresentation'

/** One row of the registry this caller may install and has not — in practice, an agent their
 *  ORGANIZATION published. Only the handful of fields these cards render. */
type AvailableBundle = {
  id: string
  name: string
  version: string
  description: string
  publisher?: string
  compatible: boolean
  installed: boolean
}

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

/** THE ONE Install, for the same reason `useOpenAgent` is the one Open: the fetch, the "which of
 *  these is already here" filter and the busy state all have to agree, and two copies of them
 *  would not stay in agreement.
 *
 *  `onDisk` is the roster this window already has. A bundle is offered only when the registry says
 *  it is not installed AND it is not on disk under that id — the ledger and the filesystem can
 *  disagree (an agent built here and then published carries the same id as the bundle), and
 *  offering someone "install" for a thing they authored is the confusing half of that. */
function useAvailableBundles(onDisk: AgentRow[], onRefresh?: () => void): {
  available: AvailableBundle[]
  installing: string
  error: string
  install: (b: AvailableBundle) => Promise<void>
} {
  const { client } = useClient()
  const [available, setAvailable] = useState<AvailableBundle[]>([])
  const [installing, setInstalling] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    const have = new Set(onDisk.map((a) => a.id))
    client
      .request<{ bundles?: AvailableBundle[] }>('marketplace.catalog', {})
      .then((d) => {
        if (!live) return
        setAvailable((d.bundles || []).filter((b) => b && !b.installed && !have.has(b.id)))
      })
      // Silent on purpose: a daemon without the grant refuses the method, and a red banner about
      // a capability that build never had is noise on a page that is otherwise working.
      .catch(() => {})
    return () => {
      live = false
    }
  }, [client, onDisk])

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

  return { available, installing, error, install }
}

/** The shelf ITSELF — the cards, their two verbs, and the failure they can produce. Split out of
 *  the page so the launchpad could show the same shelf under its own heading while its table was
 *  being built; the old My-Agents page still draws it. */
export function AgentShelf({
  agents,
  onEdit,
  onRefresh,
}: {
  agents: AgentRow[]
  onEdit: (id: string) => void
  onRefresh?: () => void
}) {
  const { opening, error, open } = useOpenAgent()
  const { available, installing, error: installError, install } = useAvailableBundles(
    agents,
    onRefresh,
  )

  return (
    <>
      {error && <div className="page-error">{error}</div>}
      {installError && <div className="page-error">{installError}</div>}

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
                    b.compatible ? `Install ${b.name || b.id}` : 'Built for a newer agentd than this one'
                  }
                >
                  <Download size={15} />
                  {installing === b.id ? 'Installing…' : b.compatible ? 'Install' : 'Incompatible'}
                </button>
              </div>
            </div>
          ))}
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

        <AgentShelf agents={agents} onEdit={onEdit} onRefresh={onRefresh} />
      </div>
    </div>
  )
}
