import { Building2, Check, Copy, Download, ExternalLink, Globe, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { gateway } from '../gateway/client'
import type { AgentInfo, CatalogBundle } from '../gateway/protocol'
import { agentColor, agentInitials, agentTag, MAIN_AGENT_ID } from '../lib/agentPresentation'
import { useAuthSession } from '../lib/auth'
import { hostOs } from '../lib/host'
import { fetchMyOrgs, type OrgMembership } from '../lib/orgs'
import { useApp } from '../state/store'
import MarketplaceCards from './MarketplaceCards'
import { webHref } from './MarketplaceView'
import NewAgentModal from './NewAgentModal'
import PageShell from './PageShell'

/**
 * My Agents — the user's own shelf, which REPLACED the marketplace as the sidebar destination.
 *
 * The public marketplace is on hold as a product decision, but its two jobs did not go away; they
 * moved here and changed shape around the owner instead of the shopper:
 *
 *   "what agents do I have?"       — every agent on this account: the platform's and the ones the
 *                                    user (or Agent Builder) authored. From `hello.agents`, which
 *                                    is already per-account on hosted, so tenancy comes free.
 *   "how do I hand one to someone?" — the SAME two doors a store card offered, now on the owner's
 *                                    own card: the hosted web link and the standalone installer.
 *
 * PUBLISH STATE IS A JOIN, NOT A FLAG. An agent is "published" exactly when the registry lists a
 * bundle with its id — the same signed index every client verifies. Nothing here stores a boolean
 * that could drift from what the registry actually serves; if the row is in the catalog, the share
 * doors render, and if it was unlisted they disappear on the next refresh.
 *
 * ORGANIZATION AGENTS (tenancy E5) render from DATA, not client logic: rows the daemon marks
 * `scope: 'org'` group into an Organization section, named via the accounts /me/orgs answer.
 * "Share to organization" appears on personal cards only when the caller ADMINISTERS at least
 * one org — the daemon re-checks the role from the token, so the button is a convenience, never
 * the authorization.
 *
 * The catalog grid survives at the bottom as "From the platform": with the store button gone this
 * is the only place a hosted user can still ADD a platform agent to their account, and removing
 * that ability was not part of the decision to shelve the storefront.
 */

/** Everything the card needs to say about one agent the user has. */
type Shelf = {
  agent: AgentInfo
  published: CatalogBundle | null
}

const ORG_ADMIN_ROLES = new Set(['owner', 'admin'])

function shareUrl(bundle: CatalogBundle): string {
  return bundle.webUrl ? webHref(bundle.webUrl) : ''
}

function installerFor(bundle: CatalogBundle): { url: string; label: string } | null {
  const os = hostOs()
  const hit = (bundle.installers || []).find((i) => i.platform === os) || (bundle.installers || [])[0]
  return hit ? { url: hit.url, label: hit.platform } : null
}

function ShelfCard({
  row,
  adminOrgs,
  onShared,
  onError
}: {
  row: Shelf
  /** orgs the CALLER administers — share targets for a personal card, unshare right on an org one */
  adminOrgs: OrgMembership[]
  onShared: (msg: string) => void
  onError: (msg: string) => void
}): ReactNode {
  const viewAgent = useApp((s) => s.viewAgent)
  const openAgentApp = useApp((s) => s.openAgentApp)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)

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
        </div>
        {isOrg ? (
          <span className="admin-chip" title="Shared by your organization — read-only; your chats with it stay yours">
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
        {installer && (
          <a className="btn ghost" href={installer.url} title={`Download the standalone ${installer.label} installer — for someone without this platform`}>
            <Download size={14} />Installer
          </a>
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
  const installBusy = useApp((s) => s.installBusy)
  const installBundle = useApp((s) => s.installBundle)
  const uninstallBundle = useApp((s) => s.uninstallBundle)
  const refreshCatalog = useApp((s) => s.refreshCatalog)
  const session = useAuthSession()
  const [creating, setCreating] = useState(false)
  const [orgs, setOrgs] = useState<OrgMembership[]>([])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  // Org names + the caller's roles, from accounts (the daemon only knows ids). Signed-out or
  // org-less accounts get [] and the whole section simply never renders.
  useEffect(() => {
    if (!session) return
    let live = true
    fetchMyOrgs()
      .then((d) => {
        if (live) setOrgs(d.orgs)
      })
      .catch(() => {
        if (live) setOrgs([])
      })
    return () => {
      live = false
    }
  }, [session])

  const adminOrgs = useMemo(() => orgs.filter((o) => ORG_ADMIN_ROLES.has(o.role)), [orgs])
  const orgName = useMemo(() => new Map(orgs.map((o) => [o.id, o.name])), [orgs])

  const shelf: Shelf[] = useMemo(() => {
    const byId = new Map(catalog.map((b) => [b.id, b]))
    return agents
      .filter((a) => a.id !== MAIN_AGENT_ID && !a.sample)
      .map((agent) => ({ agent, published: byId.get(agent.id) || null }))
  }, [agents, catalog])

  const personal = useMemo(() => shelf.filter((r) => r.agent.scope !== 'org'), [shelf])
  // One section per owning org, in a stable order — grouped, because a person can be in more
  // than one and mixing two companies' agents under one heading misattributes both.
  const orgGroups = useMemo(() => {
    const groups = new Map<string, Shelf[]>()
    for (const row of shelf.filter((r) => r.agent.scope === 'org')) {
      const key = row.agent.orgId || ''
      groups.set(key, [...(groups.get(key) || []), row])
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [shelf])

  // The add-a-platform-agent grid: catalog rows this account does NOT already have. `installed`
  // comes from the daemon and is per-account on hosted, so this is "not on MY shelf" rather than
  // "not on this machine".
  const available = useMemo(() => catalog.filter((b) => !b.installed), [catalog])

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
      title="My Agents"
      sub="Every agent on your account — yours, your organization's, and the platform's."
      actions={actions}
    >
      {catalogError && <div className="banner banner-error">{catalogError}</div>}
      {error && <div className="banner banner-error">{error}</div>}
      {notice && <div className="banner">{notice}</div>}

      {personal.length === 0 ? (
        <div className="admin-empty">
          No agents yet. Create one, or add one from the platform below.
        </div>
      ) : (
        <div className="shelf-grid">
          {personal.map((row) => (
            <ShelfCard
              key={row.agent.id}
              row={row}
              adminOrgs={adminOrgs}
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

      {orgGroups.map(([orgId, rows]) => (
        <div key={orgId} className="settings-group shelf-available">
          <div className="settings-section">
            <Building2 size={14} /> {orgName.get(orgId) || 'Organization'}
          </div>
          <div className="shelf-grid">
            {rows.map((row) => (
              <ShelfCard
                key={row.agent.id}
                row={row}
                adminOrgs={adminOrgs}
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
        </div>
      ))}

      {available.length > 0 && (
        <div className="settings-group shelf-available">
          <div className="settings-section">From the platform</div>
          <MarketplaceCards
            bundles={available}
            busy={installBusy}
            onInstall={(id) => void installBundle(id)}
            onUninstall={(id) => void uninstallBundle(id)}
            installTarget="your account"
            webHref={webHref}
            filtered={false}
          />
        </div>
      )}

      {creating && <NewAgentModal onClose={() => setCreating(false)} />}
    </PageShell>
  )
}
