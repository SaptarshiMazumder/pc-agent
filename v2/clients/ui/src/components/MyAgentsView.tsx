import { Check, Copy, Download, ExternalLink, Globe, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { gateway } from '../gateway/client'
import type { AgentInfo, CatalogBundle } from '../gateway/protocol'
import { agentColor, agentInitials, agentTag, MAIN_AGENT_ID } from '../lib/agentPresentation'
import { installerUrl } from '../lib/artifacts'
import { useAuthSession } from '../lib/auth'
import { hostOs } from '../lib/host'
import { fetchMyOrgs, fetchOrgDetail, type OrgMembership } from '../lib/orgs'
import { listableAgents } from '../lib/standaloneApps'
import { useApp } from '../state/store'
import { webHref } from './MarketplaceView'
import NewAgentModal from './NewAgentModal'
import PageShell from './PageShell'

/**
 * Agents — ONE shelf of every agent this account can use, whoever made it.
 *
 * There is a single tab now, not two. "Authored", "installed" and (for a team) "shared into the
 * organization" all land in this one list; the distinction between them is drawn on the card, by
 * two facts the daemon already sends per agent:
 *
 *   the AUTHOR   — who made this copy. On an org share `owner` is the whole company, so the maker
 *                  would be lost; the roster carries `author` (an account id) to keep it, and the
 *                  card renders "by <them>" (their email when the org roster is in hand, else the
 *                  id — "labelled by their user id" either way). Your own work reads "by you".
 *   EXTERNAL     — did this come from outside your world. For a team, "your world" is the org, so
 *                  anything that is not an org agent is external. For an individual it is your own
 *                  authorship, so an installed/curated copy is external. One tag, two boundaries.
 *
 * WHAT LEFT. The public-marketplace grid ("From the platform") and its install/uninstall buttons
 * are gone — the storefront is on hold, and an enterprise wants its own agents, not a catalogue.
 * `catalog` stays read ONLY as the published-state join: an agent shows its web link + installer
 * exactly when the signed registry lists a bundle for its id, never from a stored flag.
 *
 * The card is exported (ShelfCard) because the same doors — Open, App, Web, Installer, and the
 * share/unshare controls — are the org page's too.
 */

/** Everything the card needs to say about one agent the user has. */
export type Shelf = {
  agent: AgentInfo
  published: CatalogBundle | null
}

const ORG_ADMIN_ROLES = new Set(['owner', 'admin'])

/** A short, human-ish rendering of an account id when no email is known — "labelled by their
 *  user id" without printing the full opaque string. `acct_9f3c…` reads as a person, not a hash. */
function shortId(id: string): string {
  const s = String(id || '')
  return s.length > 10 ? `${s.slice(0, 9)}…` : s
}

function shareUrl(bundle: CatalogBundle): string {
  return bundle.webUrl ? webHref(bundle.webUrl) : ''
}

function installerFor(bundle: CatalogBundle): { url: string; label: string } | null {
  const os = hostOs()
  const hit = (bundle.installers || []).find((i) => i.platform === os) || (bundle.installers || [])[0]
  return hit ? { url: hit.url, label: hit.platform } : null
}

export function ShelfCard({
  row,
  adminOrgs,
  memberOrgs = [],
  authorLabel = '',
  external = false,
  onShared,
  onError
}: {
  row: Shelf
  /** orgs the CALLER administers — share targets for a personal card, unshare right on an org one */
  adminOrgs: OrgMembership[]
  /** orgs the caller is a plain MEMBER of — a personal card can REQUEST to share into these
   *  (an admin approves), the missing half of "only admins could put an agent in the org". */
  memberOrgs?: OrgMembership[]
  /** who made this copy, already resolved to an email / "you" / a short id ('' => don't show a
   *  byline — a personal agent whose owner the card's own chip already implies). */
  authorLabel?: string
  /** did this come from outside the caller's world (org for a team, own authorship for an
   *  individual) — renders the one "external" tag. */
  external?: boolean
  onShared: (msg: string) => void
  onError: (msg: string) => void
}): ReactNode {
  const viewAgent = useApp((s) => s.viewAgent)
  const openAgentApp = useApp((s) => s.openAgentApp)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [building, setBuilding] = useState(false)

  const { agent, published } = row
  const link = published ? shareUrl(published) : ''
  const installer = published ? installerFor(published) : null
  const isOrg = agent.scope === 'org'
  const owningOrg = isOrg ? adminOrgs.find((o) => o.id === agent.orgId) : undefined

  async function copyLink(): Promise<void> {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      // The clipboard API needs a secure context; over plain HTTP the fallback is the Open
      // button right next to this one, so a silent miss is acceptable here.
    }
  }

  async function downloadExe(): Promise<void> {
    // BUILD ON DEMAND, then download. The daemon compiles a fresh standalone installer for this
    // agent (org-scoped — never the public registry) and hands back a same-origin URL; a
    // deployment without makensis or an engine reference answers not-ready with the reason, which
    // goes to the same error line rather than a download that 404s.
    setBuilding(true)
    try {
      const r = await gateway.request<{
        ready?: boolean
        url?: string
        filename?: string
        reason?: string
      }>('agents.installer', { agentId: agent.id })
      if (r.ready && r.url) {
        const a = document.createElement('a')
        a.href = installerUrl(r.url)
        if (r.filename) a.download = r.filename
        a.rel = 'noopener'
        document.body.appendChild(a)
        a.click()
        a.remove()
      } else {
        onError(r.reason || 'the installer could not be built on this deployment yet.')
      }
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBuilding(false)
    }
  }

  async function shareTo(orgId: string): Promise<void> {
    const org = adminOrgs.find((o) => o.id === orgId)
    if (!org) return
    setBusy(true)
    try {
      const r = await gateway.request<{ shared?: boolean; error?: string }>('agents.shareToOrg', {
        agentId: agent.id,
        orgId
      })
      if (r.shared) onShared(`“${agent.name || agent.id}” is now shared with ${org.name}.`)
      else onError(r.error || 'sharing failed')
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function submitTo(orgId: string): Promise<void> {
    const org = memberOrgs.find((o) => o.id === orgId)
    if (!org) return
    setBusy(true)
    try {
      const r = await gateway.request<{ submitted?: boolean; error?: string }>(
        'agents.submitToOrg',
        { agentId: agent.id, orgId }
      )
      if (r.submitted)
        onShared(`Requested to share “${agent.name || agent.id}” with ${org.name}. An admin will review it.`)
      else onError(r.error || 'request failed')
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function unshare(): Promise<void> {
    if (!owningOrg) return
    if (!confirm(`Remove “${agent.name || agent.id}” from ${owningOrg.name}? Members lose it; nobody's chats are deleted.`)) return
    setBusy(true)
    try {
      const r = await gateway.request<{ removed?: boolean; error?: string }>(
        'agents.unshareFromOrg',
        { agentId: agent.id, orgId: owningOrg.id }
      )
      if (r.removed) onShared(`“${agent.name || agent.id}” removed from ${owningOrg.name}.`)
      else onError(r.error || 'removing failed')
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="shelf-card">
      <div className="shelf-head">
        <span className="avatar shelf-avatar" style={{ background: agentColor(agent.color, agent.id) }}>
          {agentInitials(agent.name, agent.id)}
        </span>
        <div className="shelf-title-wrap">
          <div className="shelf-title">{agent.name || agent.id}</div>
          <div className="shelf-sub">{agent.tagline || agentTag(agent.id)}</div>
          {authorLabel && (
            <div className="shelf-by" title={`Authored by ${authorLabel}`}>
              by {authorLabel}
            </div>
          )}
        </div>
        <div className="shelf-chips">
          {external && (
            <span className="admin-chip" title="From outside your world — an installed copy, or (in a team) an agent that isn't your organization's">
              external
            </span>
          )}
          {isOrg ? (
            <span className="admin-chip" title="Shared by your organization — everyone in it can use it; your chats with it stay yours">
              organization
            </span>
          ) : published ? (
            <span className="admin-chip admin-chip-ok" title={`Listed on this platform's registry at version ${published.version}`}>
              published · v{published.version}
            </span>
          ) : (
            <span className="admin-chip" title="Only on this account — publish it from Agent Builder to get share links">
              private
            </span>
          )}
        </div>
      </div>

      <div className="shelf-actions">
        <button className="btn" onClick={() => viewAgent(agent.id)} title="Open this agent's page — chats, files, settings">
          Open
        </button>
        {agent.app && (
          <button className="btn" onClick={() => openAgentApp(agent.id)} title="Open this agent's own app UI">
            <ExternalLink size={14} />App
          </button>
        )}
        {link && (
          <>
            <a className="btn" href={link} target="_blank" rel="noreferrer" title="Run it in the browser — send this link to anyone">
              <Globe size={14} />Web
            </a>
            <button className="btn ghost" onClick={() => void copyLink()} title="Copy the shareable web link">
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy link'}
            </button>
          </>
        )}
        {installer ? (
          <a className="btn ghost" href={installer.url} title={`Download the standalone ${installer.label} installer — for someone without this platform`}>
            <Download size={14} />Installer
          </a>
        ) : (
          agent.app && (
            // BUILD-ON-DEMAND exe — for an org or personal agent that was never published to the
            // public registry. The daemon compiles a standalone installer for it and serves it back.
            <button
              className="btn ghost"
              disabled={building}
              onClick={() => void downloadExe()}
              title="Build and download a standalone Windows installer — hand this agent to someone who has no platform"
            >
              <Download size={14} />
              {building ? 'Building…' : 'Download exe'}
            </button>
          )
        )}
        {!isOrg && agent.mine !== false && adminOrgs.length > 0 && (
          <select
            className="admin-select"
            disabled={busy}
            value=""
            title="Install a copy into an organization you administer — every member gets it, read-only"
            onChange={(e) => {
              if (e.target.value) void shareTo(e.target.value)
              e.target.value = ''
            }}
          >
            <option value="">Share to org…</option>
            {adminOrgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        )}
        {!isOrg && agent.mine !== false && memberOrgs.length > 0 && (
          <select
            className="admin-select"
            disabled={busy}
            value=""
            title="Submit this agent to an organization you belong to — an admin approves before members get it"
            onChange={(e) => {
              if (e.target.value) void submitTo(e.target.value)
              e.target.value = ''
            }}
          >
            <option value="">Request to share…</option>
            {memberOrgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        )}
        {isOrg && owningOrg && (
          <button className="btn danger" disabled={busy} onClick={() => void unshare()} title={`Remove this agent from ${owningOrg.name}`}>
            Remove from org
          </button>
        )}
      </div>
    </div>
  )
}

/** A stable empty list. `|| []` INSIDE a zustand selector builds a new array on every call, and
 *  zustand compares selector results with Object.is — so a fresh `[]` never equals the previous
 *  one and the component re-renders forever. It only bit once this page became a URL: reaching it
 *  by clicking meant `hello` had already arrived, while a cold load at /agents mounts before the
 *  daemon connects, `hello` is null, and the loop hits React's update-depth limit (error #185). */
const NO_AGENTS: NonNullable<ReturnType<typeof useApp.getState>['hello']>['agents'] = []

export default function MyAgentsView() {
  const agents = useApp((s) => s.hello?.agents) ?? NO_AGENTS
  const catalog = useApp((s) => s.catalog)
  const catalogError = useApp((s) => s.catalogError)
  const refreshCatalog = useApp((s) => s.refreshCatalog)
  const session = useAuthSession()
  const [creating, setCreating] = useState(false)
  const [orgs, setOrgs] = useState<OrgMembership[]>([])
  // author account id -> email, best-effort. Filled from org detail (which names members for an
  // admin); a plain member gets none and a card falls back to the short id. Module-free local
  // state so it clears with the session, never bleeding one account's roster into the next.
  const [emails, setEmails] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  // Org names + the caller's roles, from accounts (the daemon only knows ids). Signed-out or
  // org-less accounts get [] and every org-only affordance (author emails, the enterprise
  // boundary) simply falls away.
  useEffect(() => {
    if (!session) {
      setOrgs([])
      setEmails({})
      return
    }
    let live = true
    fetchMyOrgs()
      .then(async (d) => {
        if (!live) return
        setOrgs(d.orgs)
        const map: Record<string, string> = {}
        await Promise.all(
          d.orgs.map((o) =>
            fetchOrgDetail(o.id)
              .then((det) => {
                for (const m of det.members || []) if (m.accountId) map[m.accountId] = m.email || ''
              })
              .catch(() => {})
          )
        )
        if (live) setEmails(map)
      })
      .catch(() => {
        if (live) {
          setOrgs([])
          setEmails({})
        }
      })
    return () => {
      live = false
    }
  }, [session])

  const adminOrgs = useMemo(() => orgs.filter((o) => ORG_ADMIN_ROLES.has(o.role)), [orgs])
  const memberOrgs = useMemo(() => orgs.filter((o) => !ORG_ADMIN_ROLES.has(o.role)), [orgs])
  // ENTERPRISE = the caller belongs to any org. It flips the "external" boundary from "not my own
  // authorship" (individual) to "not an org agent" (team) — a single line, computed once.
  const enterprise = orgs.length > 0
  const myId = session?.accountId || ''

  // ONE shelf — authored, installed and org-shared together. `listableAgents` still drops the
  // product's own standalone surfaces (Agent Builder et al.); main and samples are filtered out
  // as before. No personal/org split anymore: the card, not the page, draws that line.
  const shelf: Shelf[] = useMemo(() => {
    const byId = new Map(catalog.map((b) => [b.id, b]))
    return listableAgents(agents)
      .filter((a) => a.id !== MAIN_AGENT_ID && !a.sample)
      .map((agent) => ({ agent, published: byId.get(agent.id) || null }))
  }, [agents, catalog])

  // Who made a copy, resolved for display; '' when the card's own chip already says whose it is.
  const authorLabelFor = (a: AgentInfo): string => {
    const author = a.author || ''
    if (author) return author === myId ? 'you' : emails[author] || shortId(author)
    // No stamped author: a personal row where the owner IS the maker. Yours reads "you"; a
    // shared/curated copy carrying nobody's name shows no byline rather than a guess.
    if (a.mine !== false && a.scope !== 'org') return 'you'
    return ''
  }
  // Outside the caller's world: for a team that is "not an org agent", for an individual it is an
  // installed/curated copy (not something they authored).
  const isExternal = (a: AgentInfo): boolean =>
    enterprise ? a.scope !== 'org' : a.origin === 'installed' || a.origin === 'curated'

  const actions = (
    <>
      <button className="btn primary" onClick={() => setCreating(true)} title="Create a new agent">
        <Plus size={15} />New agent
      </button>
      <button className="btn" onClick={() => void refreshCatalog()} title="Re-read the registry">
        <RefreshCw size={15} />
      </button>
    </>
  )

  return (
    <PageShell
      title="Agents"
      sub={
        enterprise
          ? 'Every agent you can use — your own and your organization’s. Each card names its author.'
          : 'Every agent you can use — the ones you created or installed.'
      }
      actions={actions}
    >
      {catalogError && <div className="banner banner-error">{catalogError}</div>}
      {error && <div className="banner banner-error">{error}</div>}
      {notice && <div className="banner">{notice}</div>}

      {shelf.length === 0 ? (
        <div className="admin-empty">No agents yet. Create one to get started.</div>
      ) : (
        <div className="shelf-grid">
          {shelf.map((row) => (
            <ShelfCard
              key={row.agent.id}
              row={row}
              adminOrgs={adminOrgs}
              memberOrgs={memberOrgs}
              authorLabel={authorLabelFor(row.agent)}
              external={isExternal(row.agent)}
              onShared={(m) => {
                setNotice(m)
                setError('')
              }}
              onError={(m) => {
                setError(m)
                setNotice('')
              }}
            />
          ))}
        </div>
      )}

      {creating && <NewAgentModal onClose={() => setCreating(false)} />}
    </PageShell>
  )
}
