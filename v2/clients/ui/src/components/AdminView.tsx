import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Ban,
  BadgeCheck,
  BarChart3,
  Check,
  CircleDollarSign,
  KeyRound,
  Package,
  RefreshCw,
  RotateCw,
  Search,
  ShieldCheck,
  Trash2,
  Users,
  Wallet
} from 'lucide-react'

import * as api from '../lib/admin'
import PageShell from './PageShell'

/**
 * The platform control plane.
 *
 * ONE PAGE, SIX PANELS, and no route of its own: the app has a single `view` union and this is one
 * more member of it, so an admin lands here the same way they land on Settings. The nav entry that
 * points here is rendered only for admins (see ProfileMenu), and this component refuses on its own
 * as well — a hidden button is not access control, and the server refuses regardless.
 *
 * EVERY PANEL RE-READS ON MOUNT AND AFTER EVERY MUTATION. Nothing is cached and nothing is
 * optimistically updated, because the entire purpose of the page is deciding what to do about what
 * it says: a stale balance or a stale creator state is worse here than a moment's wait.
 *
 * DESTRUCTIVE ACTIONS SAY WHAT THEY WILL DO, in the words a person would use, and confirm first.
 * The server's own refusal sentences are shown verbatim rather than being re-worded from a status
 * code — it already explains itself better than a client can from a 409.
 */

type Tab = 'overview' | 'users' | 'usage' | 'agents' | 'creators' | 'money' | 'keys'

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: 'overview', label: 'Overview', icon: <Activity size={16} /> },
  { id: 'users', label: 'Users', icon: <Users size={16} /> },
  { id: 'usage', label: 'Usage', icon: <BarChart3 size={16} /> },
  { id: 'agents', label: 'Agents', icon: <Package size={16} /> },
  { id: 'creators', label: 'Creators', icon: <BadgeCheck size={16} /> },
  { id: 'money', label: 'Money', icon: <CircleDollarSign size={16} /> },
  { id: 'keys', label: 'Keys', icon: <KeyRound size={16} /> }
]

// --------------------------------------------------------------------------- shared bits

const usd = (n: number): string => `$${(n || 0).toFixed(n && Math.abs(n) < 1 ? 4 : 2)}`
const num = (n: number): string => (n || 0).toLocaleString()
const day = (ts: number | string): string => {
  if (!ts) return '—'
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleDateString()
}

/** One async panel's lifecycle: load, reload, surface the error in words. */
function usePanel<T>(load: () => Promise<T>, deps: unknown[] = []): {
  data: T | null
  error: string
  busy: boolean
  reload: () => void
} {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [tick, setTick] = useState(0)
  // `load` is redefined every render by its caller; depending on it directly would loop forever.
  const run = useCallback(load, deps)

  useEffect(() => {
    let live = true
    setBusy(true)
    setError('')
    run()
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setBusy(false))
    return () => {
      live = false
    }
  }, [run, tick])

  return { data, error, busy, reload: () => setTick((t) => t + 1) }
}

function Panel({
  title,
  actions,
  error,
  busy,
  children
}: {
  title: string
  actions?: ReactNode
  error?: string
  busy?: boolean
  /** Optional: an error-only panel has nothing to render below the message. */
  children?: ReactNode
}): ReactNode {
  return (
    <div className="settings-group">
      <div className="admin-panel-head">
        <div className="settings-section">{title}</div>
        <div className="admin-panel-actions">
          {busy && <span className="admin-muted">loading…</span>}
          {actions}
        </div>
      </div>
      {error ? <div className="admin-error">{error}</div> : children}
    </div>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }): ReactNode {
  return (
    <div className="admin-stat">
      <div className="admin-stat-label">{label}</div>
      <div className="admin-stat-value">{value}</div>
      {sub && <div className="admin-stat-sub">{sub}</div>}
    </div>
  )
}

function Table({ head, children }: { head: string[]; children: ReactNode }): ReactNode {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function Empty({ children }: { children: ReactNode }): ReactNode {
  return <div className="admin-empty">{children}</div>
}

// --------------------------------------------------------------------------- overview

function OverviewPanel(): ReactNode {
  const { data, error, busy, reload } = usePanel(() => api.overview(), [])
  return (
    <>
      <Panel
        title={`This month${data ? ` · ${data.month}` : ''}`}
        busy={busy}
        error={error}
        actions={
          <button className="btn ghost" onClick={reload} title="Re-read the numbers">
            <RefreshCw size={14} />
          </button>
        }
      >
        <div className="admin-stats">
          <Stat label="Accounts" value={num(data?.accounts_total || 0)} sub={`${num(data?.accounts_active || 0)} active`} />
          <Stat label="Admins" value={num(data?.admins || 0)} />
          <Stat label="Model calls" value={num(data?.calls || 0)} />
          <Stat label="Provider cost" value={usd(data?.cost_usd || 0)} />
          <Stat
            label="Tokens"
            value={num((data?.in_tokens || 0) + (data?.out_tokens || 0))}
            sub={`${num(data?.in_tokens || 0)} in · ${num(data?.out_tokens || 0)} out`}
          />
          <Stat
            label="Credits outstanding"
            value={num(data?.credits_outstanding || 0)}
            sub={`${num(data?.credits_spent || 0)} spent this month`}
          />
        </div>
      </Panel>

      <Panel title="Busiest agents" busy={busy}>
        {data?.top_agents?.length ? (
          <Table head={['Agent', 'Calls', 'In', 'Out', 'Cost']}>
            {data.top_agents.map((a) => (
              <tr key={a.agent_id}>
                <td className="admin-strong">{a.agent_id}</td>
                <td>{num(a.calls)}</td>
                <td>{num(a.in_tokens)}</td>
                <td>{num(a.out_tokens)}</td>
                <td>{usd(a.cost_usd)}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>No model calls recorded this month.</Empty>
        )}
      </Panel>

      <Panel title="Biggest spenders" busy={busy}>
        {data?.top_accounts?.length ? (
          <Table head={['Account', 'Calls', 'Cost']}>
            {data.top_accounts.map((a) => (
              <tr key={a.account_id}>
                <td className="admin-strong">{a.email || a.account_id}</td>
                <td>{num(a.calls)}</td>
                <td>{usd(a.cost_usd)}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>Nothing spent this month.</Empty>
        )}
      </Panel>
    </>
  )
}

// --------------------------------------------------------------------------- users

function UsersPanel(): ReactNode {
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState('')
  if (selected) return <UserDetail id={selected} onBack={() => setSelected('')} />
  return <UserList q={q} setQ={setQ} onOpen={setSelected} />
}

function UserList({
  q,
  setQ,
  onOpen
}: {
  q: string
  setQ: (v: string) => void
  onOpen: (id: string) => void
}): ReactNode {
  const [applied, setApplied] = useState('')
  const { data, error, busy, reload } = usePanel(() => api.listAccounts(applied), [applied])

  return (
    <Panel
      title={`Accounts${data ? ` · ${data.total}` : ''}`}
      busy={busy}
      error={error}
      actions={
        <>
          <div className="admin-search">
            <Search size={14} />
            <input
              value={q}
              placeholder="email or account id"
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && setApplied(q)}
              aria-label="Search accounts"
            />
          </div>
          <button className="btn ghost" onClick={() => setApplied(q)} title="Search">
            Search
          </button>
          <button className="btn ghost" onClick={reload} title="Reload">
            <RefreshCw size={14} />
          </button>
        </>
      }
    >
      {data?.accounts?.length ? (
        <Table head={['Email', 'Created', 'Status', 'Budget', 'Spent', 'Credits', '']}>
          {data.accounts.map((a) => (
            <tr key={a.account_id}>
              <td className="admin-strong">
                {a.email}
                {a.admin_source && (
                  <span className="admin-chip admin-chip-ok" title={`Admin (${a.admin_source})`}>
                    <ShieldCheck size={11} />admin
                  </span>
                )}
              </td>
              <td>{day(a.created_at)}</td>
              <td>
                {a.active ? (
                  <span className="admin-chip admin-chip-ok">active</span>
                ) : (
                  <span className="admin-chip admin-chip-bad">disabled</span>
                )}
              </td>
              <td>{a.budget_usd == null ? 'unlimited' : usd(a.budget_usd)}</td>
              <td>{usd(a.spent_usd)}</td>
              <td>{num(a.credits_remaining)}</td>
              <td>
                <button className="btn ghost" onClick={() => onOpen(a.account_id)} title="Open this account">
                  Manage
                </button>
              </td>
            </tr>
          ))}
        </Table>
      ) : (
        <Empty>No accounts match.</Empty>
      )}
    </Panel>
  )
}

function UserDetail({ id, onBack }: { id: string; onBack: () => void }): ReactNode {
  const { data, error, busy, reload } = usePanel(() => api.accountDetail(id), [id])
  const [note, setNote] = useState('')
  const [credits, setCredits] = useState('10000')
  const [budget, setBudget] = useState('')
  const [agentId, setAgentId] = useState('')

  async function act(what: () => Promise<unknown>, ok: string): Promise<void> {
    setNote('')
    try {
      await what()
      setNote(ok)
      reload()
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  if (error) return <Panel title="Account" error={error} />

  return (
    <>
      <Panel
        title={data?.email || id}
        busy={busy}
        actions={
          <button className="btn ghost" onClick={onBack} title="Back to the list">
            <ArrowLeft size={14} />All accounts
          </button>
        }
      >
        <div className="kv-card">
          <div className="kv-row">
            <span className="kv-key">Account id</span>
            <span className="kv-val admin-mono">{id}</span>
          </div>
          <div className="kv-row">
            <span className="kv-key">Created</span>
            <span className="kv-val">{day(data?.created_at || 0)}</span>
          </div>
          <div className="kv-row">
            <span className="kv-key">Status</span>
            <span className="kv-val">{data?.active ? 'Active' : 'Disabled — cannot sign in'}</span>
          </div>
          <div className="kv-row">
            <span className="kv-key">Spent this month</span>
            <span className="kv-val">
              {usd(data?.spent_usd || 0)}
              {data?.budget_usd != null && ` of ${usd(data.budget_usd)}`}
              {data?.over && ' — over budget'}
            </span>
          </div>
          <div className="kv-row">
            <span className="kv-key">Credits</span>
            <span className="kv-val">
              {num(data?.credits_remaining || 0)}
              {data?.credits_enforced
                ? ' — enforced: running out refuses model calls'
                : ' — never granted, so this account is on the free tier'}
            </span>
          </div>
          <div className="kv-row">
            <span className="kv-key">Admin</span>
            <span className="kv-val">
              {data?.admin_source === 'config'
                ? 'Yes — from deploy configuration, not editable here'
                : data?.admin_source === 'roster'
                  ? 'Yes'
                  : 'No'}
            </span>
          </div>
        </div>
        {note && <div className="admin-note">{note}</div>}
      </Panel>

      <Panel title="Actions">
        <div className="admin-actions">
          <div className="admin-action">
            <label htmlFor="admin-credits">Grant credits</label>
            <div className="admin-action-row">
              <input
                id="admin-credits"
                value={credits}
                onChange={(e) => setCredits(e.target.value)}
                inputMode="numeric"
              />
              <button
                className="btn primary"
                title="Add credits to this account"
                onClick={() =>
                  act(
                    () => api.grantCredits(id, Number(credits) || 0),
                    `Granted ${num(Number(credits) || 0)} credits.`
                  )
                }
              >
                <Wallet size={15} />Grant
              </button>
            </div>
            <div className="admin-hint">
              The first grant puts this account on a metered plan permanently — after it, running
              out of credits refuses model calls instead of falling through to the free tier.
            </div>
          </div>

          <div className="admin-action">
            <label htmlFor="admin-budget">Monthly cap (USD)</label>
            <div className="admin-action-row">
              <input
                id="admin-budget"
                value={budget}
                placeholder={data?.budget_usd == null ? 'unlimited' : String(data.budget_usd)}
                onChange={(e) => setBudget(e.target.value)}
                inputMode="decimal"
              />
              <button
                className="btn"
                title="Set the monthly spending cap"
                onClick={() => act(() => api.setBudget(id, Number(budget)), `Cap set to ${usd(Number(budget))}.`)}
              >
                Set
              </button>
              <button
                className="btn ghost"
                title="Remove the cap entirely"
                onClick={() => act(() => api.setBudget(id, null), 'Cap removed — this account is now unlimited.')}
              >
                Clear
              </button>
            </div>
          </div>

          <div className="admin-action">
            <label htmlFor="admin-entitlement">Agent access</label>
            <div className="admin-action-row">
              <input
                id="admin-entitlement"
                value={agentId}
                placeholder="agent id"
                onChange={(e) => setAgentId(e.target.value)}
              />
              <button
                className="btn"
                title="Give this account access to that agent"
                onClick={() => act(() => api.setEntitlement(id, agentId, true), `Granted access to ${agentId}.`)}
              >
                <Check size={15} />Grant
              </button>
              <button
                className="btn ghost"
                title="Remove access to that agent"
                onClick={() => act(() => api.setEntitlement(id, agentId, false), `Removed access to ${agentId}.`)}
              >
                Remove
              </button>
            </div>
          </div>

          <div className="admin-action">
            <label>Access</label>
            <div className="admin-action-row">
              <button
                className="btn"
                title="Sign this account out of every device"
                onClick={() => {
                  if (!confirm('Sign this account out of every device? Tokens already issued stay valid for a few more minutes.')) return
                  void act(async () => {
                    const r = await api.revokeSessions(id)
                    setNote(
                      `Revoked ${r.revoked} session(s). Already-issued access tokens keep working for up to ${r.access_tokens_valid_for_s}s.`
                    )
                  }, '')
                }}
              >
                <Trash2 size={15} />Sign out everywhere
              </button>
              {data?.active ? (
                <button
                  className="btn danger"
                  title="Disable this account — sign-in stops immediately"
                  onClick={() => {
                    if (!confirm('Disable this account? They will be signed out immediately and cannot sign back in.')) return
                    void act(() => api.setActive(id, false), 'Account disabled.')
                  }}
                >
                  <Ban size={15} />Disable
                </button>
              ) : (
                <button className="btn" title="Let this account sign in again" onClick={() => act(() => api.setActive(id, true), 'Account enabled.')}>
                  <Check size={15} />Enable
                </button>
              )}
              {data?.admin_source !== 'config' &&
                (data?.admin_source === 'roster' ? (
                  <button
                    className="btn ghost"
                    title="Remove platform admin access"
                    onClick={() => act(() => api.setAdmin(id, false), 'Admin access removed.')}
                  >
                    Remove admin
                  </button>
                ) : (
                  <button
                    className="btn ghost"
                    title="Give this account full platform admin access"
                    onClick={() => {
                      if (!confirm('Make this account a platform admin? They will be able to see and change every account, its money, and the platform keys.')) return
                      void act(() => api.setAdmin(id, true), 'Now a platform admin.')
                    }}
                  >
                    <ShieldCheck size={15} />Make admin
                  </button>
                ))}
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Usage by agent, this month">
        {data?.usage_by_agent?.length ? (
          <Table head={['Agent', 'Calls', 'In', 'Out', 'Cost']}>
            {data.usage_by_agent.map((u) => (
              <tr key={u.agent_id}>
                <td className="admin-strong">{u.agent_id}</td>
                <td>{num(u.calls)}</td>
                <td>{num(u.in_tokens)}</td>
                <td>{num(u.out_tokens)}</td>
                <td>{usd(u.cost_usd)}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>No model calls this month.</Empty>
        )}
      </Panel>

      <Panel title="Credit grants">
        {data?.grants?.length ? (
          <Table head={['Granted', 'Scope', 'Credits', 'Used', 'Class', 'Expires']}>
            {data.grants.map((g) => (
              <tr key={g.id}>
                <td>{day(g.created_at)}</td>
                <td>{g.scope}</td>
                <td>{num(g.credits)}</td>
                <td>{num(g.credits_used)}</td>
                <td>{g.credit_class}</td>
                <td>{g.expires_at ? day(g.expires_at) : 'never'}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>Never granted credits — this account is on the free tier.</Empty>
        )}
      </Panel>

      <Panel title="Devices &amp; sign-ins">
        {data?.devices?.length ? (
          <Table head={['Device', 'Client', 'First seen', 'Last used', 'State']}>
            {data.devices.map((d) => (
              <tr key={d.family_id}>
                <td className="admin-strong">{d.device_label || '—'}</td>
                <td>{d.client_id || '—'}</td>
                <td>{day(d.issued_at)}</td>
                <td>{d.used_at ? day(d.used_at) : 'never'}</td>
                <td>
                  {d.revoked ? (
                    <span className="admin-chip admin-chip-bad">revoked</span>
                  ) : (
                    <span className="admin-chip admin-chip-ok">active</span>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>Never signed in.</Empty>
        )}
      </Panel>

      <Panel title="Recent model calls">
        {data?.recent_usage?.length ? (
          <Table head={['When', 'Agent', 'Model', 'In', 'Out', 'Cost', 'Credits']}>
            {data.recent_usage.map((u, i) => (
              <tr key={`${u.ts}-${i}`}>
                <td>{new Date(u.ts * 1000).toLocaleString()}</td>
                <td>{u.agent_id || '—'}</td>
                <td>{u.model}</td>
                <td>{num(u.in_tokens)}</td>
                <td>{num(u.out_tokens)}</td>
                <td>{usd(u.cost_usd)}</td>
                <td>{num(u.credits)}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>Nothing yet.</Empty>
        )}
      </Panel>
    </>
  )
}

// --------------------------------------------------------------------------- usage

const GROUPS: { id: api.UsageGroup; label: string }[] = [
  { id: 'agent', label: 'By agent' },
  { id: 'model', label: 'By model' },
  { id: 'account', label: 'By account' },
  { id: 'day', label: 'By day' }
]

function UsagePanel(): ReactNode {
  const [group, setGroup] = useState<api.UsageGroup>('agent')
  const [month, setMonth] = useState('')
  const { data, error, busy, reload } = usePanel(() => api.usage(group, month), [group, month])

  // A day rollup is a time series, so it reads forwards; every other grouping is a ranking and
  // reads biggest-first, which is what the server already returns.
  const rows = group === 'day' ? [...(data?.rows || [])].sort((a, b) => a.key.localeCompare(b.key)) : data?.rows || []
  const total = rows.reduce((n, r) => n + r.cost_usd, 0)

  return (
    <Panel
      title="Token usage"
      busy={busy}
      error={error}
      actions={
        <>
          <select
            className="admin-select"
            value={month || data?.month || ''}
            onChange={(e) => setMonth(e.target.value)}
            aria-label="Month"
          >
            {(data?.months?.length ? data.months : [data?.month || '']).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <select
            className="admin-select"
            value={group}
            onChange={(e) => setGroup(e.target.value as api.UsageGroup)}
            aria-label="Group by"
          >
            {GROUPS.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label}
              </option>
            ))}
          </select>
          <button className="btn ghost" onClick={reload} title="Reload">
            <RefreshCw size={14} />
          </button>
        </>
      }
    >
      {rows.length ? (
        <>
          <Table head={[GROUPS.find((g) => g.id === group)?.label.replace('By ', '') || 'Key', 'Calls', 'In', 'Out', 'Cached', 'Credits', 'Cost']}>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className="admin-strong">{r.key}</td>
                <td>{num(r.calls)}</td>
                <td>{num(r.in_tokens)}</td>
                <td>{num(r.out_tokens)}</td>
                <td>{num(r.cached_tokens)}</td>
                <td>{num(r.credits)}</td>
                <td>{usd(r.cost_usd)}</td>
              </tr>
            ))}
          </Table>
          <div className="admin-hint">
            {rows.length} row(s) · {usd(total)} of provider cost in {data?.month}. Cost is what the
            provider charged us; credits are what the user was charged.
          </div>
        </>
      ) : (
        <Empty>No model calls recorded in {data?.month || 'this period'}.</Empty>
      )}
    </Panel>
  )
}

// --------------------------------------------------------------------------- agents

function AgentsPanel(): ReactNode {
  const { data, error, busy, reload } = usePanel(() => api.agents(), [])
  const usageQuery = usePanel(() => api.usage('agent'), [])
  const [note, setNote] = useState('')

  async function unlist(b: api.Bundle): Promise<void> {
    if (
      !confirm(
        `Take "${b.title || b.id}" off the marketplace?\n\n` +
          'The listing disappears from every store and nothing new can install it. The files are ' +
          'KEPT and copies already installed keep working — republishing the same version puts it ' +
          'back.'
      )
    )
      return
    setNote('')
    try {
      const r = await api.unlistAgent(b.id)
      setNote(r.message || `${b.id} is off the marketplace.`)
      reload()
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  return (
    <>
      <Panel
        title="Published agents"
        busy={busy}
        error={error}
        actions={
          <button className="btn ghost" onClick={reload} title="Re-read the registry">
            <RefreshCw size={14} />
          </button>
        }
      >
        {note && <div className="admin-note">{note}</div>}
        {!data?.configured ? (
          <Empty>This deployment has no registry configured, so nothing is published.</Empty>
        ) : data.error ? (
          <div className="admin-error">{data.error}</div>
        ) : data.bundles.length ? (
          <Table head={['Agent', 'Version', 'Creator', 'Delivery', 'Status', '']}>
            {data.bundles.map((b) => (
              <tr key={b.id}>
                <td>
                  <div className="admin-strong">{b.title || b.id}</div>
                  <div className="admin-mono admin-muted">{b.id}</div>
                </td>
                <td className="admin-mono">{b.version}</td>
                <td>{b.publisher_name || b.publisher_id || '—'}</td>
                <td>
                  {[b.delivery?.web && 'web', b.delivery?.exe && 'installer'].filter(Boolean).join(' · ') || '—'}
                </td>
                <td>
                  {b.publisher_revoked ? (
                    <span className="admin-chip admin-chip-bad" title="Its creator is revoked — this will not install">
                      creator revoked
                    </span>
                  ) : (
                    <span className="admin-chip admin-chip-ok">listed</span>
                  )}
                </td>
                <td>
                  <button
                    className="btn danger"
                    title="Take this agent off the marketplace (reversible — the files are kept)"
                    onClick={() => void unlist(b)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>The registry is reachable but lists no agents.</Empty>
        )}
      </Panel>

      {data?.configured && (
        <Panel title="Shared engine">
          {data.engines?.length ? (
            <Table head={['Platform', 'Version', 'Size', 'Digest']}>
              {data.engines.map((e, i) => (
                <tr key={`${e.platform}-${e.version}-${i}`}>
                  <td className="admin-strong">{e.platform || '—'}</td>
                  <td className="admin-mono">{e.version || '—'}</td>
                  <td>{e.size ? `${Math.round(e.size / 1_000_000)} MB` : '—'}</td>
                  <td className="admin-mono admin-muted">
                    {e.sha256 ? `${e.sha256.slice(0, 16)}…` : 'missing — no stub can verify what it runs'}
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <Empty>
              No engine is published, so per-agent installers cannot be built — anyone without
              agentd already installed has no way in.
            </Empty>
          )}
          <div className="kv-card">
            <div className="kv-row">
              <span className="kv-key">Web host</span>
              <span className="kv-val admin-mono">{data.web?.host || '—'}</span>
            </div>
            <div className="kv-row">
              <span className="kv-key">Registry</span>
              <span className="kv-val admin-mono">{data.registry_url}</span>
            </div>
          </div>
        </Panel>
      )}

      <Panel title="Token usage by agent, this month" busy={usageQuery.busy} error={usageQuery.error}>
        {usageQuery.data?.rows?.length ? (
          <Table head={['Agent', 'Calls', 'In', 'Out', 'Cached', 'Credits', 'Cost']}>
            {usageQuery.data.rows.map((r) => (
              <tr key={r.key}>
                <td className="admin-strong">{r.key}</td>
                <td>{num(r.calls)}</td>
                <td>{num(r.in_tokens)}</td>
                <td>{num(r.out_tokens)}</td>
                <td>{num(r.cached_tokens)}</td>
                <td>{num(r.credits)}</td>
                <td>{usd(r.cost_usd)}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>No model calls recorded this month.</Empty>
        )}
      </Panel>
    </>
  )
}

// --------------------------------------------------------------------------- creators

function CreatorsPanel(): ReactNode {
  const { data, error, busy, reload } = usePanel(() => api.creators(), [])
  const [note, setNote] = useState('')

  async function act(what: () => Promise<{ message?: string }>, fallback: string): Promise<void> {
    setNote('')
    try {
      const r = await what()
      setNote(r.message || fallback)
      reload()
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  const waiting = (data?.creators || []).filter((c) => c.state === 'pending_review')

  return (
    <Panel
      title={`Creators${data ? ` · ${data.creators.length}` : ''}`}
      busy={busy}
      error={error}
      actions={
        <>
          {waiting.length > 0 && (
            <button
              className="btn primary"
              title="Admit everyone who is waiting"
              onClick={() => {
                if (!confirm(`Admit ${waiting.length} creator(s)? Their key joins the signed roster and anything they parked goes live immediately.`)) return
                void act(() => api.admitCreator(), 'Admitted.')
              }}
            >
              <BadgeCheck size={15} />Admit all ({waiting.length})
            </button>
          )}
          <button className="btn ghost" onClick={reload} title="Reload">
            <RefreshCw size={14} />
          </button>
        </>
      }
    >
      {note && <div className="admin-note">{note}</div>}
      {data?.partial && (
        <div className="admin-error">
          <AlertTriangle size={14} />
          {data.reason || 'Only creators awaiting review are shown.'}
        </div>
      )}
      {data?.creators?.length ? (
        <Table head={['Creator', 'State', 'Waiting to publish', 'Joined', 'Key', '']}>
          {data.creators.map((c) => (
            <tr key={c.creator_id}>
              <td>
                <div className="admin-strong">{c.name || c.creator_id}</div>
                <div className="admin-mono admin-muted">{c.creator_id}</div>
              </td>
              <td>
                {c.state === 'listed' && <span className="admin-chip admin-chip-ok">listed</span>}
                {c.state === 'pending_review' && <span className="admin-chip admin-chip-warn">waiting</span>}
                {c.state === 'revoked' && <span className="admin-chip admin-chip-bad">revoked</span>}
              </td>
              <td>
                {c.parked?.length
                  ? c.parked.map((p) => p.bundle_id).join(', ')
                  : c.state === 'pending_review'
                    ? 'nothing yet'
                    : '—'}
              </td>
              <td>{day(c.admitted || c.created)}</td>
              <td>{c.wrapped ? <span className="admin-chip admin-chip-ok">wrapped</span> : <span className="admin-chip admin-chip-bad">unwrapped</span>}</td>
              <td>
                {c.state === 'pending_review' && (
                  <button
                    className="btn"
                    title="Admit this creator and publish what they parked"
                    onClick={() => {
                      if (!confirm(`Admit ${c.name || c.creator_id}? Their key joins the signed roster and anything they parked goes live immediately.`)) return
                      void act(() => api.admitCreator(c.creator_id), 'Admitted.')
                    }}
                  >
                    Admit
                  </button>
                )}
                {c.state === 'listed' && (
                  <button
                    className="btn danger"
                    title="Revoke this creator"
                    onClick={() => {
                      if (!confirm(`Revoke ${c.name || c.creator_id}? Every client refuses everything they signed on its next check. Copies already installed are not removed.`)) return
                      void act(() => api.revokeCreator(c.creator_id), 'Revoked.')
                    }}
                  >
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      ) : data?.partial ? (
        <Empty>Nobody is awaiting review right now.</Empty>
      ) : (
        <Empty>No creators yet. The first person to press Publish appears here, waiting.</Empty>
      )}
    </Panel>
  )
}

// --------------------------------------------------------------------------- money

function MoneyPanel(): ReactNode {
  const products = usePanel(() => api.listProducts(), [])
  const books = usePanel(() => api.ledger(), [])
  const [note, setNote] = useState('')

  async function toggle(p: api.Product): Promise<void> {
    setNote('')
    try {
      await api.saveProduct({ ...p, active: !p.active })
      products.reload()
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  return (
    <>
      <Panel title="On sale" busy={products.busy} error={products.error}>
        {note && <div className="admin-note">{note}</div>}
        {products.data?.products?.length ? (
          <Table head={['Product', 'Kind', 'Price', 'Credits', 'Agent', 'Subscribers', '']}>
            {products.data.products.map((p) => (
              <tr key={p.id}>
                <td>
                  <div className="admin-strong">{p.title || p.id}</div>
                  <div className="admin-mono admin-muted">{p.id}</div>
                </td>
                <td>{p.kind === 'credit_pack' ? 'credit pack' : 'agent subscription'}</td>
                <td>{usd(p.price_usd)}</td>
                <td>{num(p.credits)}</td>
                <td>{p.agent_id || '—'}</td>
                <td>{num(p.subscribers)}</td>
                <td>
                  <button
                    className="btn ghost"
                    title={p.active ? 'Stop selling this' : 'Put this back on sale'}
                    onClick={() => void toggle(p)}
                  >
                    {p.active ? 'Withdraw' : 'List'}
                  </button>
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>Nothing is for sale.</Empty>
        )}
      </Panel>

      <Panel title="Books" busy={books.busy} error={books.error}>
        {books.data && (
          <>
            {!books.data.balanced && (
              <div className="admin-error">
                <AlertTriangle size={14} /> The books do not balance ({usd(books.data.residual_usd)} residual). A
                posting bypassed the ledger — this is a correctness bug, not a display one.
              </div>
            )}
            <div className="admin-stats">
              <Stat label="Gross margin" value={usd(books.data.gross_margin_usd)} />
              {Object.entries(books.data.accounts).map(([name, v]) => (
                <Stat key={name} label={name.replace(/_/g, ' ')} value={usd(v)} />
              ))}
            </div>
          </>
        )}
      </Panel>

      <Panel title="Recent postings" busy={books.busy}>
        {books.data?.entries?.length ? (
          <Table head={['When', 'Type', 'Account', 'Dir', 'Amount', 'Ref']}>
            {books.data.entries.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.ts * 1000).toLocaleString()}</td>
                <td>{e.txn_type}</td>
                <td>{e.account}</td>
                <td>{e.direction}</td>
                <td>{usd(e.amount_usd)}</td>
                <td className="admin-mono admin-muted">{e.ref || '—'}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty>No money has moved yet.</Empty>
        )}
      </Panel>
    </>
  )
}

// --------------------------------------------------------------------------- keys

function KeysPanel(): ReactNode {
  const { data, error, busy, reload } = usePanel(() => api.keys(), [])
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState('')
  const [value, setValue] = useState('')

  async function save(): Promise<void> {
    setNote('')
    try {
      const r = await api.setSecret(editing, value)
      setNote(r.rolled.length ? `${editing} updated. Rolling: ${r.rolled.join(', ')}.` : `${editing} updated. ${r.note}`)
      if (r.roll_errors.length) setNote((n) => `${n} Some services did not roll: ${r.roll_errors.join('; ')}`)
      setEditing('')
      setValue('')
      reload()
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  return (
    <>
      {note && <div className="admin-note">{note}</div>}

      <Panel
        title="Token signing keys"
        busy={busy}
        error={error}
        actions={
          <button
            className="btn"
            title="Mint a new signing key and retire the current one"
            onClick={() => {
              if (!confirm('Rotate the token signing key? Nobody is signed out — the old key stays valid for an hour while the new one signs.')) return
              void (async () => {
                try {
                  const r = await api.rotateSigningKey()
                  setNote(`New signing key ${r.kid}. The previous one stays valid for ${r.previous_key_valid_for_s}s, so no session breaks.`)
                  reload()
                } catch (e) {
                  setNote((e as Error).message)
                }
              })()
            }}
          >
            <RotateCw size={15} />Rotate
          </button>
        }
      >
        <Table head={['Key id', 'Algorithm', 'State', 'At rest', 'Created', 'Retires']}>
          {(data?.signing_keys || []).map((k) => (
            <tr key={k.kid}>
              <td className="admin-mono admin-strong">{k.kid}</td>
              <td>{k.alg}</td>
              <td>
                {k.active ? (
                  <span className="admin-chip admin-chip-ok">signing</span>
                ) : (
                  <span className="admin-chip">verify only</span>
                )}
              </td>
              <td>
                {k.encrypted ? (
                  <span className="admin-chip admin-chip-ok">encrypted</span>
                ) : (
                  <span className="admin-chip admin-chip-warn">plain</span>
                )}
              </td>
              <td>{day(k.created_at)}</td>
              <td>{k.expires_at ? day(k.expires_at) : '—'}</td>
            </tr>
          ))}
        </Table>
        {data && !data.signing_key_kek && (
          <div className="admin-hint">
            <AlertTriangle size={13} /> AGENTD_IDENTITY_KEK is not set, so signing keys are stored
            unencrypted. Never change it once tokens are live — it decrypts the stored key, and
            changing it signs everyone out.
          </div>
        )}
      </Panel>

      <Panel title="Platform &amp; provider keys">
        {!data?.secrets.configured ? (
          <Empty>No platform secret is configured for this deployment.</Empty>
        ) : data.secrets.error ? (
          <div className="admin-error">{data.secrets.error}</div>
        ) : (
          <>
            <Table head={['Key', 'State', 'Used by', '']}>
              {(data.secrets.keys || []).map((k) => (
                <tr key={k.name}>
                  <td className="admin-mono admin-strong">{k.name}</td>
                  <td>
                    {k.placeholder ? (
                      <span className="admin-chip admin-chip-warn">placeholder</span>
                    ) : k.set ? (
                      <span className="admin-chip admin-chip-ok">set</span>
                    ) : (
                      <span className="admin-chip admin-chip-bad">empty</span>
                    )}
                  </td>
                  <td className="admin-muted">{k.consumers.length ? k.consumers.join(', ') : 'nothing reads this'}</td>
                  <td>
                    <button
                      className="btn ghost"
                      title="Replace this key's value"
                      onClick={() => {
                        setEditing(k.name)
                        setValue('')
                      }}
                    >
                      Replace
                    </button>
                  </td>
                </tr>
              ))}
            </Table>
            <div className="admin-hint">
              Values are never shown here — only whether a key is set. Changed{' '}
              {data.secrets.last_changed ? day(data.secrets.last_changed) : 'never'}.
            </div>
            {editing && (
              <div className="admin-action">
                <label htmlFor="admin-secret">New value for {editing}</label>
                <div className="admin-action-row">
                  <input
                    id="admin-secret"
                    type="password"
                    value={value}
                    autoComplete="off"
                    onChange={(e) => setValue(e.target.value)}
                  />
                  <button className="btn primary" disabled={!value} onClick={() => void save()} title="Save and roll the services that read it">
                    Save &amp; roll
                  </button>
                  <button className="btn ghost" onClick={() => setEditing('')} title="Cancel">
                    Cancel
                  </button>
                </div>
                <div className="admin-hint">
                  Saving also restarts the services that read this key. Without that restart the new
                  value is stored and nothing uses it.
                </div>
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel title="Signing keys for the marketplace">
        {!data?.creator_keys.configured ? (
          <Empty>No creator key table is configured for this deployment.</Empty>
        ) : data.creator_keys.error ? (
          <div className="admin-error">{data.creator_keys.error}</div>
        ) : (
          <>
            <Table head={['Holder', 'State', 'Public key', 'At rest', 'Since']}>
              {(data.creator_keys.keys || []).map((k) => (
                <tr key={k.creator_id}>
                  <td>
                    <div className="admin-strong">{k.name || k.creator_id}</div>
                    <div className="admin-mono admin-muted">{k.creator_id}</div>
                  </td>
                  <td>
                    {k.state === 'root' ? (
                      <span className="admin-chip admin-chip-ok" title="The platform root key — pinned in every installed client">
                        platform root
                      </span>
                    ) : k.state === 'listed' ? (
                      <span className="admin-chip admin-chip-ok">listed</span>
                    ) : k.state === 'revoked' ? (
                      <span className="admin-chip admin-chip-bad">revoked</span>
                    ) : (
                      <span className="admin-chip admin-chip-warn">{k.state}</span>
                    )}
                  </td>
                  <td className="admin-mono admin-muted">{k.public_key.slice(0, 16)}…</td>
                  <td>
                    {k.wrapped ? (
                      <span className="admin-chip admin-chip-ok">KMS-wrapped</span>
                    ) : (
                      <span className="admin-chip admin-chip-bad">unwrapped</span>
                    )}
                  </td>
                  <td>{day(k.admitted || k.created)}</td>
                </tr>
              ))}
            </Table>
            <div className="admin-hint">
              Wrapped with {data.creator_keys.kms_key || 'no KMS key'}. Private halves are never
              read by this page. Losing the platform root key cannot be recovered — every installed
              client pins it.
            </div>
          </>
        )}
      </Panel>
    </>
  )
}

// --------------------------------------------------------------------------- page

export default function AdminView(): ReactNode {
  const [tab, setTab] = useState<Tab>('overview')
  const who = usePanel(() => api.whoami(), [])

  // The server refuses regardless — this is so a non-admin who reaches the view by any route sees
  // an explanation instead of six panels of identical 403s.
  if (who.data && !who.data.is_admin) {
    return (
      <PageShell title="Admin" sub="The platform control plane.">
        <Empty>This account does not have admin access.</Empty>
      </PageShell>
    )
  }

  return (
    <PageShell
      title="Admin"
      sub="Users, agents, creators, money and keys for the whole platform."
      nav={
        <>
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`settings-nav-item ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
              title={t.label}
            >
              {t.icon}
              <span>{t.label}</span>
            </button>
          ))}
        </>
      }
    >
      {who.error && <div className="admin-error">{who.error}</div>}
      {tab === 'overview' && <OverviewPanel />}
      {tab === 'users' && <UsersPanel />}
      {tab === 'usage' && <UsagePanel />}
      {tab === 'agents' && <AgentsPanel />}
      {tab === 'creators' && <CreatorsPanel />}
      {tab === 'money' && <MoneyPanel />}
      {tab === 'keys' && <KeysPanel />}
    </PageShell>
  )
}
