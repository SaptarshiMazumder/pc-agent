import {
  Building2,
  Check,
  Copy,
  Globe,
  Link2,
  Plus,
  RefreshCw,
  Trash2,
  Users
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { awaitGrant, fetchCatalog, onCreditsChanged, purchase, useAuthSession, type Catalog, type CreditPack } from '../lib/auth'
import {
  createOrg,
  fetchMyOrgs,
  fetchOrgDetail,
  fetchOrgUsage,
  joinOrg,
  mintInvite,
  updateDomain,
  updateMember,
  type MyOrgs,
  type OrgDetail,
  type OrgUsageRow
} from '../lib/orgs'
import { gateway } from '../gateway/client'
import { useApp } from '../state/store'
import PageShell from './PageShell'

/**
 * Organizations (tenancy plan E5) — two pages behind one view id:
 *
 *   viewedOrgId === ''  the OVERVIEW: my orgs, the domain-matched join offers, create, and the
 *                       invite-code redeem box (the joiner is not a member yet, so this is the
 *                       one surface they can redeem from).
 *   viewedOrgId set     ONE org's page: members, seats, invites, domains, usage — the admin
 *                       vocabulary of the platform console, scoped to what the SERVER says this
 *                       caller may see (a plain member gets no member list; the render follows
 *                       the payload, never its own idea of the role).
 *
 * Everything talks to the accounts service via lib/orgs.ts; the daemon learns membership from
 * the token, so nothing here needs to tell it anything.
 */

const ADMIN_ROLES = new Set(['owner', 'admin'])

function Overview(): JSX.Element {
  const viewOrg = useApp((s) => s.viewOrg)
  const [data, setData] = useState<MyOrgs | null>(null)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    fetchMyOrgs()
      .then((d) => setData(d))
      .catch((e: Error) => setError(e.message))
  }, [])
  useEffect(reload, [reload])

  async function doCreate(): Promise<void> {
    if (!name.trim()) return
    setBusy(true)
    setError('')
    try {
      const org = await createOrg(name.trim())
      setName('')
      viewOrg(org.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function doJoin(input: { inviteToken?: string; orgId?: string }): Promise<void> {
    setBusy(true)
    setError('')
    try {
      const org = await joinOrg(input)
      setInvite('')
      viewOrg(org.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <PageShell
      title="Organizations"
      sub="Shared agents and a shared credit pool for a team — your chats stay your own."
      actions={
        <button className="btn" onClick={reload} title="Reload organizations">
          <RefreshCw size={15} />
        </button>
      }
    >
      {error && <div className="banner banner-error">{error}</div>}

      <div className="settings-group">
        <div className="settings-section">Your organizations</div>
        {!data || data.orgs.length === 0 ? (
          <div className="admin-empty">You are not in an organization yet.</div>
        ) : (
          <div className="agents-list">
            {data.orgs.map((o) => (
              <button
                key={o.id}
                className="row"
                onClick={() => viewOrg(o.id)}
                title={`Open ${o.name}`}
              >
                <span className="avatar">
                  <Building2 size={15} />
                </span>
                <span className="row-main">
                  <span className="row-title">{o.name}</span>
                  <span className="row-sub">{o.role}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {data && data.joinable.length > 0 && (
        <div className="settings-group">
          <div className="settings-section">Matches your email domain</div>
          {data.joinable.map((o) => (
            <div key={o.id} className="org-join-row">
              <span>{o.name}</span>
              <button
                className="btn"
                disabled={busy}
                onClick={() => void doJoin({ orgId: o.id })}
                title={`Join ${o.name} — offered because of your email domain`}
              >
                Join
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="settings-group">
        <div className="settings-section">Join with an invite</div>
        <div className="org-inline-form">
          <input
            className="input"
            value={invite}
            placeholder="Paste an invite code"
            onChange={(e) => setInvite(e.target.value)}
          />
          <button
            className="btn"
            disabled={busy || !invite.trim()}
            onClick={() => void doJoin({ inviteToken: invite.trim() })}
            title="Redeem the invite code"
          >
            <Link2 size={14} />
            Join
          </button>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-section">Create an organization</div>
        <div className="org-inline-form">
          <input
            className="input"
            value={name}
            placeholder="Organization name"
            onChange={(e) => setName(e.target.value)}
          />
          <button
            className="btn primary"
            disabled={busy || !name.trim()}
            onClick={() => void doCreate()}
            title="Create it — you become its owner"
          >
            <Plus size={14} />
            Create
          </button>
        </div>
      </div>
    </PageShell>
  )
}

function Detail({ orgId }: { orgId: string }): JSX.Element {
  const viewOrg = useApp((s) => s.viewOrg)
  const session = useAuthSession()
  const [org, setOrg] = useState<OrgDetail | null>(null)
  const [usage, setUsage] = useState<OrgUsageRow[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [domain, setDomain] = useState('')
  const [inviteLink, setInviteLink] = useState('')
  const [copied, setCopied] = useState(false)

  const reload = useCallback(() => {
    setError('')
    // Clear the previous account's org before refetching, or a switch to a NEW user (who is not a
    // member of this org) leaves the last account's org — name, members, DOMAINS — on screen until
    // a fetch that then 404s. `session` is in the deps for exactly this: refetch when the ACCOUNT
    // changes, not only when the org id does. This is the "old domain stayed after I switched
    // users" bug — the detail was keyed to orgId alone and never re-read on sign-out/sign-in.
    setOrg(null)
    setUsage(null)
    fetchOrgDetail(orgId)
      .then((d) => {
        setOrg(d)
        if (ADMIN_ROLES.has(d.role)) {
          fetchOrgUsage(orgId)
            .then((u) => setUsage(u.members))
            .catch(() => setUsage(null))
        }
      })
      .catch((e: Error) => setError(e.message))
  }, [orgId, session])
  useEffect(reload, [reload])

  const isAdmin = !!org && ADMIN_ROLES.has(org.role)

  async function act(work: () => Promise<unknown>, done = ''): Promise<void> {
    setError('')
    setNotice('')
    try {
      await work()
      if (done) setNotice(done)
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function doInvite(): Promise<void> {
    setError('')
    try {
      const inv = await mintInvite(orgId)
      setInviteLink(inv.inviteToken)
      setCopied(false)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function copyInvite(): Promise<void> {
    try {
      await navigator.clipboard.writeText(inviteLink)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      /* shown in the box right next to the button — copying by hand still works */
    }
  }

  if (error && !org) {
    return (
      <PageShell title="Organization" sub={orgId}>
        <div className="banner banner-error">{error}</div>
      </PageShell>
    )
  }
  if (!org) {
    return (
      <PageShell title="Organization" sub={orgId}>
        <div className="admin-empty">Loading…</div>
      </PageShell>
    )
  }

  const usageByAccount = new Map((usage || []).map((u) => [u.accountId, u]))

  return (
    <PageShell
      title={org.name}
      sub={`${org.seatsUsed} of ${org.seatsTotal} seats · you are ${org.role}`}
      actions={
        <>
          <button className="btn" onClick={() => viewOrg('')} title="All organizations">
            <Users size={15} />
            All orgs
          </button>
          <button className="btn" onClick={reload} title="Reload">
            <RefreshCw size={15} />
          </button>
        </>
      }
    >
      {error && <div className="banner banner-error">{error}</div>}
      {notice && <div className="banner">{notice}</div>}

      {/* THE ORG'S AGENTS MOVED to the unified Agents tab (one list for everything the caller can
          use, each card labelled by its author). The org page keeps only what is org MANAGEMENT:
          the admin approval queue below, then seats, credits, members and domains. */}
      {isAdmin && (
        <PendingShareRequests
          orgId={orgId}
          onNotice={(m) => {
            setNotice(m)
            setError('')
          }}
          onError={(m) => {
            setError(m)
            setNotice('')
          }}
        />
      )}

      {isAdmin && (
        <div className="settings-group">
          <div className="settings-section">Credit pool</div>
          <div className="org-pool-line">
            <span
              className="admin-chip admin-chip-ok"
              title="Credits granted to this organization, spendable by members on its agents"
            >
              {org.poolCreditsRemaining ?? 0} credits remaining
            </span>
          </div>
        </div>
      )}

      {/* THE ORG'S SHOP — seats and pool top-ups, bought BY an admin FOR the org. Same rules as
          the personal store: only a product_id goes up, and `checkoutUrl` in the answer decides
          whether the customer goes somewhere (Stripe) or the goods are already delivered. */}
      {isAdmin && <OrgShop orgId={orgId} onBought={reload} />}

      {isAdmin && org.members && (
        <div className="settings-group">
          <div className="settings-section">Members</div>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Member</th>
                <th>Role</th>
                <th>Monthly cap</th>
                <th>This month</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {org.members.map((m) => {
                const spent = usageByAccount.get(m.accountId)
                const isPrimary = m.accountId === org.primaryOwner
                const self = session?.accountId === m.accountId
                return (
                  <tr key={m.accountId}>
                    <td>
                      {m.email || m.accountId}
                      {isPrimary && (
                        <span className="admin-chip" title="The organization's recovery anchor — cannot be demoted or removed">
                          primary owner
                        </span>
                      )}
                    </td>
                    <td>
                      {isPrimary || (m.role === 'owner' && org.role !== 'owner') ? (
                        m.role
                      ) : (
                        <select
                          className="admin-select"
                          value={m.role}
                          title="Change this member's role"
                          onChange={(e) =>
                            void act(
                              () => updateMember(orgId, m.accountId, { role: e.target.value }),
                              'Role updated.'
                            )
                          }
                        >
                          <option value="member">member</option>
                          <option value="admin">admin</option>
                          {org.role === 'owner' && <option value="owner">owner</option>}
                        </select>
                      )}
                    </td>
                    <td>
                      <input
                        className="input org-cap-input"
                        type="number"
                        min={0}
                        defaultValue={m.monthlyCreditCap || 0}
                        title="Monthly org-credit cap for this member — 0 means uncapped"
                        onBlur={(e) => {
                          const cap = Math.max(0, Number(e.target.value) || 0)
                          if (cap !== m.monthlyCreditCap)
                            void act(
                              () => updateMember(orgId, m.accountId, { monthlyCreditCap: cap }),
                              'Cap updated.'
                            )
                        }}
                      />
                    </td>
                    <td>{spent ? `${spent.credits} credits · ${spent.calls} calls` : '—'}</td>
                    <td>
                      {!isPrimary && !self && (m.role !== 'owner' || org.role === 'owner') && (
                        <button
                          className="btn danger"
                          title="Remove from the organization — their seat frees up; their own chats and account are untouched"
                          onClick={() => {
                            if (confirm(`Remove ${m.email || m.accountId} from ${org.name}?`))
                              void act(
                                () => updateMember(orgId, m.accountId, { active: false }),
                                'Member removed.'
                              )
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="org-inline-form">
            <button className="btn" onClick={() => void doInvite()} title="Mint a single-use invite code (7 days)">
              <Plus size={14} />
              New invite
            </button>
            {inviteLink && (
              <>
                <input className="input" readOnly value={inviteLink} title="Shown once — send it to the person joining" />
                <button className="btn ghost" onClick={() => void copyInvite()} title="Copy the invite code">
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="settings-group">
          <div className="settings-section">Allowed email domains</div>
          <div className="pmenu-desc">
            Anyone signing in with a matching email is <em>offered</em> this organization — never
            added silently.
          </div>
          {(org.domains || []).map((d) => (
            <div key={d} className="org-join-row">
              <span>
                <Globe size={14} /> {d}
              </span>
              <button
                className="btn danger"
                title={`Stop offering ${org.name} to @${d} emails`}
                onClick={() => void act(() => updateDomain(orgId, d, true), 'Domain removed.')}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          <div className="org-inline-form">
            <input
              className="input"
              value={domain}
              placeholder="example.co.jp"
              onChange={(e) => setDomain(e.target.value)}
            />
            <button
              className="btn"
              disabled={!domain.trim()}
              title="Allow this domain (no verification yet — type carefully)"
              onClick={() =>
                void act(() => updateDomain(orgId, domain.trim()), 'Domain added.').then(() =>
                  setDomain('')
                )
              }
            >
              <Plus size={14} />
              Allow
            </button>
          </div>
        </div>
      )}

      {!isAdmin && (
        <div className="settings-group">
          <div className="settings-section">Membership</div>
          <div className="admin-empty">
            You are a member of {org.name}. Its shared agents are on the <strong>Agents</strong>{' '}
            tab, alongside your own — open any to chat; your chats stay yours alone.
          </div>
        </div>
      )}
    </PageShell>
  )
}

export default function OrgView(): JSX.Element {
  const viewedOrgId = useApp((s) => s.viewedOrgId)
  return viewedOrgId ? <Detail orgId={viewedOrgId} /> : <Overview />
}


type ShareRequest = {
  agentId: string
  agentName: string
  requesterEmail: string
  requesterAccountId: string
  submittedAt: number
}

/* THE APPROVAL QUEUE — agents members submitted, waiting for an admin. Approve promotes the
   already-staged copy into the org (identical end state to a direct share); Reject drops it.
   Admin-only, and the server re-checks the role, so this is the convenience surface, not the
   authorization. Refreshes on the same agents.changed broadcast the roster uses, so an approve
   here and a new submission elsewhere both land without a manual reload. */
function PendingShareRequests({
  orgId,
  onNotice,
  onError
}: {
  orgId: string
  onNotice: (msg: string) => void
  onError: (msg: string) => void
}): JSX.Element | null {
  const agents = useApp((s) => s.hello?.agents) // a proxy for "the roster changed" — re-reads below
  const [requests, setRequests] = useState<ShareRequest[]>([])
  const [busy, setBusy] = useState('')

  const reload = useCallback(() => {
    void gateway
      .request<{ requests?: ShareRequest[] }>('agents.listOrgShareRequests', { orgId })
      .then((r) => setRequests(r.requests || []))
      .catch(() => setRequests([]))
  }, [orgId])
  // Re-read on mount, on org change, and whenever the roster broadcast fires (a submit/approve).
  useEffect(reload, [reload, agents])

  async function decide(agentId: string, approve: boolean): Promise<void> {
    setBusy(agentId)
    try {
      const method = approve ? 'agents.approveOrgShare' : 'agents.rejectOrgShare'
      const r = await gateway.request<{ approved?: boolean; rejected?: boolean; error?: string }>(
        method,
        { agentId, orgId }
      )
      if (r.approved) onNotice(`Approved “${agentId}” — every member has it now.`)
      else if (r.rejected) onNotice(`Rejected the request for “${agentId}”.`)
      else onError(r.error || 'action failed')
      reload()
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  if (requests.length === 0) return null

  return (
    <div className="settings-group">
      <div className="settings-section">Pending share requests ({requests.length})</div>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Requested by</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {requests.map((req) => (
            <tr key={req.agentId}>
              <td>{req.agentName || req.agentId}</td>
              <td>{req.requesterEmail || req.requesterAccountId || 'a member'}</td>
              <td className="admin-row-actions">
                <button
                  className="btn primary"
                  disabled={!!busy}
                  onClick={() => void decide(req.agentId, true)}
                  title="Approve — install it into the org for every member, read-only"
                >
                  Approve
                </button>
                <button
                  className="btn"
                  disabled={!!busy}
                  onClick={() => void decide(req.agentId, false)}
                  title="Reject — drop the request; nothing is shared"
                >
                  Reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* Seats and pool top-ups for one org. Kept beside the seat count and the pool because that is
   where an admin looks when either runs out. */
function OrgShop({ orgId, onBought }: { orgId: string; onBought: () => void }): JSX.Element {
  const [seats, setSeats] = useState<Catalog | null>(null)
  const [packs, setPacks] = useState<Catalog | null>(null)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    void fetchCatalog('seat_subscription').then(setSeats)
    void fetchCatalog('credit_pack').then(setPacks)
    return onCreditsChanged(onBought)
  }, [onBought])

  async function buy(p: CreditPack): Promise<void> {
    setBusy(p.id)
    setNote('')
    setError('')
    try {
      const r = await purchase(p.id, orgId)
      if (r.checkoutUrl) {
        window.open(r.checkoutUrl, '_blank', 'noopener')
        setNote('Complete the payment in the opened tab — this page updates automatically.')
        void awaitGrant()
        return
      }
      setNote(
        p.seats > 0
          ? `Added ${p.seats} seats. ${r.paymentDetail}`
          : `Added ${r.credits.toLocaleString()} credits to the pool. ${r.paymentDetail}`,
      )
      onBought()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const shelf = (catalog: Catalog | null, empty: string): JSX.Element =>
    catalog === null ? (
      <div className="admin-empty">Loading…</div>
    ) : catalog.packs.length === 0 ? (
      <div className="admin-empty">{empty}</div>
    ) : (
      <>
        {catalog.packs.map((p) => (
          <div key={p.id} className="org-join-row">
            <span>
              <strong>
                {p.seats > 0 ? `${p.seats} seats` : `${p.credits.toLocaleString()} credits`}
              </strong>
              {p.seats > 0 && p.periodDays > 0 ? ` · renews every ${p.periodDays} days` : ''}
            </span>
            <button className="btn primary" disabled={!!busy} onClick={() => void buy(p)}>
              {busy === p.id ? 'Buying…' : `Buy · $${p.priceUsd.toFixed(2)}`}
            </button>
          </div>
        ))}
        {catalog.paymentNote && <div className="pmenu-desc">{catalog.paymentNote}</div>}
      </>
    )

  return (
    <>
      <div className="settings-group">
        <div className="settings-section">Buy seats</div>
        <div className="pmenu-desc">Each seat lets one more person join. Renews until cancelled.</div>
        {shelf(seats, 'No seat products are configured on this environment.')}
      </div>
      <div className="settings-group">
        <div className="settings-section">Top up the pool</div>
        <div className="pmenu-desc">
          The pool is what every member spends, bounded by their monthly cap.
        </div>
        {shelf(packs, 'No credit packs are configured on this environment.')}
      </div>
      {note && <div className="banner">{note}</div>}
      {error && <div className="banner banner-error">{error}</div>}
    </>
  )
}
