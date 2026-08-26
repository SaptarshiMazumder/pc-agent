/* Organizations and seats — agentd's page, copied.
 *
 * COPIED VERBATIM in behaviour from clients/ui/src/components/OrgView.tsx. Do not edit;
 * `validate_agent` compares it against the source. An enterprise buys seats once, and the people
 * in it meet the same two pages in the assistant and inside every agent it runs.
 *
 * TWO PAGES BEHIND ONE COMPONENT:
 *
 *   no org selected   the OVERVIEW: my orgs, the domain-matched join offers, create, and the
 *                     invite-code box — the joiner is not a member yet, so this is the one
 *                     surface they can redeem from.
 *   an org selected   ONE org: members, seats, invites, domains, usage — scoped to what the
 *                     SERVER says this caller may see.
 *
 * THE RENDER FOLLOWS THE PAYLOAD, NEVER ITS OWN IDEA OF THE ROLE. The accounts service sends
 * `members` only to an admin, so its presence IS the permission. A client that decided for itself
 * what to show would be a second, weaker copy of a rule already enforced where it counts.
 *
 * THREE THINGS DIFFER FROM THE ASSISTANT'S COPY, all of them dependencies an agent does not have:
 *
 *   1. NO ICON SET. agentd has lucide; a scaffolded agent's package.json has react and react-dom
 *      and nothing else. The icons there were decorative, so these are text buttons rather than a
 *      dependency added to every agent ever built.
 *   2. NO HOST STORE. agentd keeps the selected org in its zustand store because its router needs
 *      it; here it is local state, so dropping this module into any window needs no wiring.
 *   3. NO PageShell. Its own markup instead, so the module owns its layout the way settings and
 *      credits own theirs.
 */

import { authStatus, billing, createOrg, fetchMyOrgs, fetchOrgDetail, fetchOrgUsage, joinOrg, mintInvite, onCreditsChanged, updateDomain, updateMember } from '@agentd/client'
import type { Catalog, CreditPack } from '@agentd/client'
import type { AgentdClient, MyOrgs, OrgDetail, OrgUsageRow } from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'

import './orgs.css'

const ADMIN_ROLES = new Set(['owner', 'admin'])

export default function OrgView({ client }: { client?: AgentdClient }) {
  // Local, not lifted. See the module note: this component is droppable into any window.
  const [orgId, setOrgId] = useState('')
  return orgId ? (
    <Detail client={client} orgId={orgId} onBack={() => setOrgId('')} />
  ) : (
    <Overview client={client} onOpen={setOrgId} />
  )
}

function Overview({ client, onOpen }: { client?: AgentdClient; onOpen: (id: string) => void }) {
  const [data, setData] = useState<MyOrgs | null>(null)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    void fetchMyOrgs({ client })
      .then(setData)
      .catch((e: Error) => setError(e.message))
  }, [client])
  useEffect(reload, [reload])

  async function doCreate(): Promise<void> {
    if (!name.trim()) return
    setBusy(true)
    setError('')
    try {
      const org = await createOrg(name.trim(), undefined, { client })
      setName('')
      onOpen(org.id)
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
      const org = await joinOrg(input, { client })
      setInvite('')
      onOpen(org.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="orgs-page">
      <div className="orgs-head">
        <div className="orgs-title">Organizations</div>
        <div className="orgs-sub">
          Shared agents and a shared credit pool for a team — your chats stay your own.
        </div>
      </div>

      <div className="orgs-body">
        {error && <div className="orgs-banner orgs-banner-error">{error}</div>}

        <section className="orgs-group">
          <div className="orgs-section">Your organizations</div>
          <div className="orgs-card">
            {!data || data.orgs.length === 0 ? (
              <div className="orgs-empty">You are not in an organization yet.</div>
            ) : (
              data.orgs.map((o) => (
                <button key={o.id} className="orgs-row orgs-row-click" onClick={() => onOpen(o.id)}>
                  <span className="orgs-row-main">
                    <span className="orgs-row-title">{o.name}</span>
                    <span className="orgs-row-sub">{o.role}</span>
                  </span>
                </button>
              ))
            )}
          </div>
        </section>

        {/* OFFERED, never applied silently — the server matches the domain, the person still
            chooses. A domain match that joined you automatically would put your usage inside
            somebody else's org without you ever agreeing to it. */}
        {data && data.joinable.length > 0 && (
          <section className="orgs-group">
            <div className="orgs-section">Matches your email domain</div>
            <div className="orgs-card">
              {data.joinable.map((o) => (
                <div key={o.id} className="orgs-row">
                  <span>{o.name}</span>
                  <button
                    className="ghost-btn"
                    disabled={busy}
                    onClick={() => void doJoin({ orgId: o.id })}
                    title={`Join ${o.name} — offered because of your email domain`}
                  >
                    Join
                  </button>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="orgs-group">
          <div className="orgs-section">Join with an invite</div>
          <div className="orgs-card">
            <div className="orgs-form">
              <input
                className="orgs-input"
                value={invite}
                placeholder="Paste an invite code"
                onChange={(e) => setInvite(e.target.value)}
              />
              <button
                className="ghost-btn"
                disabled={busy || !invite.trim()}
                onClick={() => void doJoin({ inviteToken: invite.trim() })}
              >
                Join
              </button>
            </div>
          </div>
        </section>

        <section className="orgs-group">
          <div className="orgs-section">Create an organization</div>
          <div className="orgs-card">
            <div className="orgs-form">
              <input
                className="orgs-input"
                value={name}
                placeholder="Organization name"
                onChange={(e) => setName(e.target.value)}
              />
              <button
                className="prime-btn"
                disabled={busy || !name.trim()}
                onClick={() => void doCreate()}
                title="Create it — you become its owner"
              >
                Create
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

function Detail({
  client,
  orgId,
  onBack,
}: {
  client?: AgentdClient
  orgId: string
  onBack: () => void
}) {
  const [org, setOrg] = useState<OrgDetail | null>(null)
  const [usage, setUsage] = useState<OrgUsageRow[] | null>(null)
  const [me, setMe] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [domain, setDomain] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [copied, setCopied] = useState(false)

  // WHO AM I, so the member table can refuse to let somebody remove their own seat. agentd reads
  // this off its session store; here it comes from the same status call everything else uses.
  useEffect(() => {
    void authStatus({ client })
      .then((s) => setMe(s.accountId))
      .catch(() => setMe(''))
  }, [client])

  const reload = useCallback(() => {
    setError('')
    void fetchOrgDetail(orgId, { client })
      .then((d) => {
        setOrg(d)
        if (ADMIN_ROLES.has(d.role)) {
          void fetchOrgUsage(orgId, { client })
            .then((u) => setUsage(u.members))
            .catch(() => setUsage(null))
        }
      })
      .catch((e: Error) => setError(e.message))
  }, [orgId, client])
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
      const inv = await mintInvite(orgId, {}, { client })
      setInviteCode(inv.inviteToken)
      setCopied(false)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function copyInvite(): Promise<void> {
    try {
      await navigator.clipboard.writeText(inviteCode)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      /* the code is in the box right next to the button — copying by hand still works */
    }
  }

  if (error && !org) {
    return (
      <div className="orgs-page">
        <div className="orgs-head">
          <div className="orgs-title">Organization</div>
          <div className="orgs-sub">{orgId}</div>
        </div>
        <div className="orgs-body">
          <div className="orgs-banner orgs-banner-error">{error}</div>
          <button className="ghost-btn" onClick={onBack}>
            All organizations
          </button>
        </div>
      </div>
    )
  }
  if (!org) {
    return (
      <div className="orgs-page">
        <div className="orgs-head">
          <div className="orgs-title">Organization</div>
        </div>
        <div className="orgs-body">
          <div className="orgs-empty">Loading…</div>
        </div>
      </div>
    )
  }

  const spentBy = new Map((usage || []).map((u) => [u.accountId, u]))

  return (
    <div className="orgs-page">
      <div className="orgs-head">
        <div className="orgs-title">{org.name}</div>
        <div className="orgs-sub">
          {org.seatsUsed} of {org.seatsTotal} seats · you are {org.role}
        </div>
      </div>

      <div className="orgs-body">
        <button className="ghost-btn" onClick={onBack}>
          All organizations
        </button>

        {error && <div className="orgs-banner orgs-banner-error">{error}</div>}
        {notice && <div className="orgs-banner">{notice}</div>}

        {isAdmin && (
          <section className="orgs-group">
            <div className="orgs-section">Credit pool</div>
            <div className="orgs-card">
              <div className="orgs-chip" title="Credits granted to this organization, spendable by members on its agents">
                {org.poolCreditsRemaining ?? 0} credits remaining
              </div>
            </div>
          </section>
        )}

        {/* Money, where the seats are counted. Admin-only like the sections around it —
            presence of the admin payload is the permission; see the module note. */}
        {isAdmin && <OrgShop client={client} orgId={orgId} onBought={reload} />}

        {/* `org.members` ARRIVES ONLY FOR AN ADMIN — see the module note. */}
        {isAdmin && org.members && (
          <section className="orgs-group">
            <div className="orgs-section">Members</div>
            <div className="orgs-card">
              <div className="orgs-scroll">
                <table className="orgs-table">
                  <thead>
                    <tr>
                      <th>Member</th>
                      <th>Role</th>
                      <th>Monthly cap</th>
                      <th>This month</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {org.members.map((m) => {
                      const spent = spentBy.get(m.accountId)
                      const isPrimary = m.accountId === org.primaryOwner
                      const self = !!me && me === m.accountId
                      return (
                        <tr key={m.accountId}>
                          <td>
                            {m.email || m.accountId}
                            {isPrimary && (
                              <span className="orgs-chip" title="The organization's recovery anchor — cannot be demoted or removed">
                                primary owner
                              </span>
                            )}
                          </td>
                          <td>
                            {isPrimary || (m.role === 'owner' && org.role !== 'owner') ? (
                              m.role
                            ) : (
                              <select
                                className="orgs-select"
                                value={m.role}
                                onChange={(e) =>
                                  void act(
                                    () => updateMember(orgId, m.accountId, { role: e.target.value }, { client }),
                                    'Role updated.',
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
                              className="orgs-input orgs-cap"
                              type="number"
                              min={0}
                              defaultValue={m.monthlyCreditCap || 0}
                              title="Monthly org-credit cap for this member — 0 means uncapped"
                              onBlur={(e) => {
                                const cap = Math.max(0, Number(e.target.value) || 0)
                                if (cap !== m.monthlyCreditCap)
                                  void act(
                                    () => updateMember(orgId, m.accountId, { monthlyCreditCap: cap }, { client }),
                                    'Cap updated.',
                                  )
                              }}
                            />
                          </td>
                          <td>{spent ? `${spent.credits} credits · ${spent.calls} calls` : '—'}</td>
                          <td>
                            {!isPrimary && !self && (m.role !== 'owner' || org.role === 'owner') && (
                              <button
                                className="ghost-btn orgs-danger"
                                title="Remove from the organization — their seat frees up; their own chats and account are untouched"
                                onClick={() => {
                                  if (confirm(`Remove ${m.email || m.accountId} from ${org.name}?`))
                                    void act(
                                      () => updateMember(orgId, m.accountId, { active: false }, { client }),
                                      'Member removed.',
                                    )
                                }}
                              >
                                Remove
                              </button>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="orgs-form">
                <button className="ghost-btn" onClick={() => void doInvite()} title="Mint a single-use invite code (7 days)">
                  New invite
                </button>
                {inviteCode && (
                  <>
                    <input className="orgs-input" readOnly value={inviteCode} title="Shown once — send it to the person joining" />
                    <button className="ghost-btn" onClick={() => void copyInvite()}>
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </section>
        )}

        {isAdmin && (
          <section className="orgs-group">
            <div className="orgs-section">Allowed email domains</div>
            <p className="orgs-help">
              Anyone signing in with a matching email is <em>offered</em> this organization — never
              added silently.
            </p>
            <div className="orgs-card">
              {(org.domains || []).map((d) => (
                <div key={d} className="orgs-row">
                  <span>{d}</span>
                  <button
                    className="ghost-btn orgs-danger"
                    title={`Stop offering ${org.name} to @${d} emails`}
                    onClick={() => void act(() => updateDomain(orgId, d, true, { client }), 'Domain removed.')}
                  >
                    Remove
                  </button>
                </div>
              ))}
              <div className="orgs-form">
                <input
                  className="orgs-input"
                  value={domain}
                  placeholder="example.co.jp"
                  onChange={(e) => setDomain(e.target.value)}
                />
                <button
                  className="ghost-btn"
                  disabled={!domain.trim()}
                  title="Allow this domain (no verification yet — type carefully)"
                  onClick={() =>
                    void act(() => updateDomain(orgId, domain.trim(), false, { client }), 'Domain added.').then(
                      () => setDomain(''),
                    )
                  }
                >
                  Allow
                </button>
              </div>
            </div>
          </section>
        )}

        {!isAdmin && (
          <section className="orgs-group">
            <div className="orgs-section">Membership</div>
            <div className="orgs-card">
              <div className="orgs-empty">
                You are a member of {org.name}. Its shared agents are available to you; your chats
                with them stay yours alone.
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  )
}


/* The organization's shop: seats and pool top-ups, bought BY an admin FOR the org.

   HERE AND NOT ON THE CREDITS PAGE, because the credits page is the individual's view and an org
   member's reads "your organization funds your usage". Seats and the pool are org facts, so they
   are bought where the seats are counted and the pool is shown.

   THE SAME RULES AS THE PERSONAL SHOP: only a product_id ever goes up (prices live in the
   products table), one idempotency key per press, and `checkoutUrl` in the answer IS the
   question "does the rail need the customer to go somewhere" — followed when present, balance
   shown otherwise, correct on the test rail and on Stripe without asking which is configured. */
function OrgShop({
  client,
  orgId,
  onBought,
}: {
  client?: AgentdClient
  orgId: string
  /** Re-read the org: seats moved, or the pool did. */
  onBought: () => void
}) {
  const [seatCatalog, setSeatCatalog] = useState<Catalog | null>(null)
  const [packCatalog, setPackCatalog] = useState<Catalog | null>(null)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const shop = billing({ client })
    void shop.catalog('seat_subscription').then(setSeatCatalog)
    void shop.catalog('credit_pack').then(setPackCatalog)
    // A purchase that finished on a card rail lands back here via the return URL; the balance
    // event is what tells this page the pool moved without anyone pressing Refresh.
    return onCreditsChanged(onBought)
  }, [client, onBought])

  async function buy(p: CreditPack): Promise<void> {
    setBusy(p.id)
    setNote('')
    setError('')
    try {
      const r = await billing({ client }).buy(p.id, location.href.split('#')[0], orgId)
      if (r.checkoutUrl) {
        location.href = r.checkoutUrl
        return
      }
      setNote(
        p.seats > 0
          ? `Added ${p.seats} seat${p.seats === 1 ? '' : 's'}. ${r.paymentDetail}`
          : `Added ${r.credits.toLocaleString()} credits to the pool. ${r.paymentDetail}`,
      )
      onBought()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy('')
    }
  }

  const shelf = (catalog: Catalog | null, empty: string) =>
    catalog === null ? (
      <div className="orgs-empty">Loading…</div>
    ) : catalog.packs.length === 0 ? (
      <div className="orgs-empty">{empty}</div>
    ) : (
      <>
        {catalog.packs.map((p) => (
          <div key={p.id} className="orgs-row">
            <span>
              <b>{p.seats > 0 ? `${p.seats} seat${p.seats === 1 ? '' : 's'}` : `${p.credits.toLocaleString()} credits`}</b>
              {p.title ? ` — ${p.title}` : ''}
              {p.periodDays > 0 && p.seats > 0 ? ` · renews every ${p.periodDays} days` : ''}
            </span>
            <button className="prime-btn" disabled={!!busy} onClick={() => void buy(p)}>
              {busy === p.id ? 'Buying…' : `Buy · $${p.priceUsd.toFixed(2)}`}
            </button>
          </div>
        ))}
        {catalog.paymentNote && <div className="orgs-help">{catalog.paymentNote}</div>}
      </>
    )

  return (
    <>
      <section className="orgs-group">
        <div className="orgs-section">Seats</div>
        <p className="orgs-help">
          Each seat lets one more person join. Bought as a subscription — it renews until
          cancelled.
        </p>
        <div className="orgs-card">{shelf(seatCatalog, 'No seat products are configured on this environment.')}</div>
      </section>
      <section className="orgs-group">
        <div className="orgs-section">Top up the pool</div>
        <p className="orgs-help">
          The pool is what every member spends, each bounded by their seat’s monthly allowance.
        </p>
        <div className="orgs-card">{shelf(packCatalog, 'No credit packs are configured on this environment.')}</div>
      </section>
      {note && <div className="orgs-banner">{note}</div>}
      {error && <div className="orgs-banner orgs-banner-error">{error}</div>}
    </>
  )
}
