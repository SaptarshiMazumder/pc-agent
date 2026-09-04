/* The launchpad's "Your agents" table — the shelf's cards laid flat, one row per agent.
 *
 * WHAT EACH COLUMN IS, AND WHAT IT IS NOT. The redesign draws four data columns; this table
 * renders the three that exist and omits the one that does not:
 *
 *   who     avatar + name + tagline — straight off the roster row.
 *   shape   the design shows the TEMPLATE (Chat / Dashboard / Workbench / Headless), but
 *           `create_agent` never persists its `template` argument, so the granular fact is
 *           gone by the time the roster is read. What IS on the row is whether the agent has a
 *           working window (`app`, filled in only when the entry file is really on disk) — so
 *           the column says Window or Headless, which is true, rather than guessing a template,
 *           which would be a lie that styles itself as data.
 *   state   the design shows build results ("building", "2 notes", "build failed") — none of
 *           which the daemon records anywhere this window can read. The row DOES carry
 *           `version`, and a missing one is the single most actionable fact about an agent
 *           (publishing refuses it), so the column reports exactly that: vX with a check, or
 *           "no version" with the warn mark.
 *   (time)  the design's fourth column is a relative timestamp. AgentRow carries no time of any
 *           kind, so the column is ABSENT — not rendered empty, not faked from nothing.
 *
 * The filter pills follow the same rule: All is real (it is the unfiltered roster), and
 * Drafts / Published are rendered where the design puts them but disabled, because nothing
 * records whether an agent has ever been published. A pill that filtered on a guess would
 * show a wrong list with a confident label.
 *
 * Open and Edit are the SAME two verbs as everywhere else — Edit via the caller's editAgent,
 * Open via useOpenAgent, the one implementation. Open is ABSENT, never disabled, on an agent
 * with no window: there is nothing the click could ever do (see MyAgentsView's header note).
 */

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  ExternalLink,
  Monitor,
  Pencil,
  RefreshCw,
} from 'lucide-react'
import { hasWindow } from '../agentd/app-window'
import { agentAuthorLabel, agentIsExternal, type AgentRow } from '../agentd/roster'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { useAuthorship } from '../lib/authorship'
import { useOpenAgent } from './MyAgentsView'

export function LaunchpadAgentTable({
  agents,
  searching = false,
  onEdit,
  onRefresh,
}: {
  agents: AgentRow[]
  /** True while the page's search has a query — an empty table then means "no matches", which
   *  is a different fact from "no agents yet" and gets different words (the sidebar's rule). */
  searching?: boolean
  onEdit: (id: string) => void
  onRefresh: () => void
}) {
  const { opening, error, open } = useOpenAgent()
  const { enterprise, myId, emails } = useAuthorship()

  return (
    <section className="lp-section">
      <div className="lp-section-bar">
        <h3 className="lp-section-head">Agents</h3>
        <div className="lp-filters">
          <button className="lp-pill active">All</button>
          {/* PLACEHOLDERS — see the header. Disabled until publish state is recorded somewhere
              this window can read; a pill that filters on a guess shows a wrong list. */}
          <button className="lp-pill" disabled title="Not tracked yet">
            Drafts
          </button>
          <button className="lp-pill" disabled title="Not tracked yet">
            Published
          </button>
        </div>
        <button
          className="icon-btn icon-btn--sm"
          title="Re-read the roster"
          aria-label="Re-read the roster"
          onClick={onRefresh}
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {error && <div className="page-error">{error}</div>}

      {agents.length === 0 ? (
        searching ? (
          <div className="empty-card">
            <p>No agents match.</p>
          </div>
        ) : (
          <div className="empty-card">
            <p>No agents yet.</p>
            <p className="row-sub">
              Start one with <strong>New agent</strong> in the sidebar — this table fills itself in.
            </p>
          </div>
        )
      ) : (
        <div className="lp-table">
          <div className="lp-tr lp-tr--head">
            <span>Agent</span>
            <span>Shape</span>
            <span>State</span>
            <span aria-hidden="true" />
          </div>
          {agents.map((a) => {
            const author = agentAuthorLabel(a, myId, emails)
            const external = agentIsExternal(a, enterprise, myId)
            return (
            <div className="lp-tr" key={a.id}>
              <span className="lp-td-who">
                <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>
                  {agentInitials(a.name, a.id)}
                </span>
                <span className="lp-td-names">
                  <span className="lp-td-name">
                    {a.name || a.id}
                    {a.scope === 'org' && (
                      <span className="lp-tag" title="Your organization's — everyone in it can use it">
                        org
                      </span>
                    )}
                    {external && (
                      <span className="lp-tag" title="From outside your world — an installed copy, or (in a team) not your organization's">
                        external
                      </span>
                    )}
                  </span>
                  {(a.tagline || a.description) && (
                    <span className="lp-td-tag">{a.tagline || a.description}</span>
                  )}
                  {author && (
                    <span className="lp-td-by" title={`Authored by ${author}`}>
                      by {author}
                    </span>
                  )}
                </span>
              </span>
              <span className="lp-td-shape">
                {hasWindow(a) ? <Monitor size={14} /> : <Clock size={14} />}
                {hasWindow(a) ? 'Window' : 'Headless'}
              </span>
              {a.version ? (
                <span className="lp-td-state ok">
                  <CheckCircle2 size={14} />v{a.version}
                </span>
              ) : (
                <span className="lp-td-state warn">
                  <AlertTriangle size={14} />
                  no version
                </span>
              )}
              <span className="lp-td-actions">
                <button
                  className="icon-btn icon-btn--sm"
                  title="Work on this agent"
                  aria-label={`Edit ${a.name || a.id}`}
                  onClick={() => onEdit(a.id)}
                >
                  <Pencil size={15} />
                </button>
                {hasWindow(a) && (
                  <button
                    className="icon-btn icon-btn--sm"
                    disabled={opening === a.id}
                    title={opening === a.id ? 'Opening…' : `Open ${a.app?.title || a.name || a.id}`}
                    aria-label={`Open ${a.name || a.id}`}
                    onClick={() => void open(a)}
                  >
                    <ExternalLink size={15} />
                  </button>
                )}
              </span>
            </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
