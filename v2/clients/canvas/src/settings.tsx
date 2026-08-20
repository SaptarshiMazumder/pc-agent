/**
 * SETTINGS, for an agent window — account, models, tools and plugins.
 *
 * I first assumed an app could not have this, and the gateway says otherwise in as many words:
 * `config.get` / `config.set` are in APP_SCOPED_METHODS precisely so "an agent app renders its own
 * settings page", with the secret-bearing fields redacted for an installed agent. So everything
 * below comes from calls an app-scoped socket is entitled to make:
 *
 *   plugins.catalog   every plugin, its tools, each tool's on/off state, the model it resolves to
 *                     today and its provider — the single richest thing an app can ask for
 *   config.get        the brain model, reasoning effort, fallbacks, the model catalog
 *   <accounts>/me/credits + the access token's own claims — who is signed in and what is left
 *
 * READ-ONLY, DELIBERATELY. Not a missing feature: on a hosted daemon `config` is the DEPLOYMENT's,
 * not this visitor's, so a settings page that wrote would let one signed-in user change the model
 * for everyone on the box. Reading is safe (and redacted); writing needs a per-account config that
 * does not exist yet. The page therefore shows what is in use and says where to change it.
 *
 * NO API KEYS, ANYWHERE. `config.get` redacts them for an installed agent, and nothing here asks
 * for one — an /apps/ page has no CSP of its own, so a key read back into it could be posted
 * anywhere. The model a tool runs on is not a secret; the credential behind it never appears.
 */

import { useCallback, useEffect, useState, type JSX } from 'react'
import { Boxes, CreditCard, Cpu, RefreshCw, User, Wrench, X } from 'lucide-react'

import type { AccountAdapter, CreditPack } from './account'

export interface SettingsClient {
  request<T = Record<string, unknown>>(method: string, params?: Record<string, unknown>): Promise<T>
}

interface CatalogTool {
  name: string
  description?: string
  enabled?: boolean
  needsModel?: boolean
  modelKind?: string
  model?: string | null
  provider?: string | null
}

interface CatalogPlugin {
  id: string
  description?: string
  enabled?: boolean
  tools?: CatalogTool[]
}

interface AgentConfig {
  agent_name?: string
  model?: string
  reasoning_effort?: string
  model_fallbacks?: unknown
  [k: string]: unknown
}

type Tab = 'account' | 'models' | 'tools'

function Row({ k, d, value }: { k: string; d?: string; value: JSX.Element | string }): JSX.Element {
  return (
    <div className="settings-row">
      <div className="settings-label">
        <div className="k">{k}</div>
        {d && <div className="d">{d}</div>}
      </div>
      <div className="kv-val">{value}</div>
    </div>
  )
}

function AccountTab({ account }: { account?: AccountAdapter }): JSX.Element {
  const [credits, setCredits] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  const read = useCallback(async () => {
    setLoading(true)
    try {
      setCredits(account?.credits ? await account.credits() : null)
    } catch {
      setCredits(null)
    } finally {
      setLoading(false)
    }
  }, [account])

  useEffect(() => { void read() }, [read])

  return (
    <>
      <div className="settings-section"><User size={13} />Signed in</div>
      <div className="settings-card">
        <Row k="Account" d="the identity every run on this window is attributed to" value={account?.email || 'unknown'} />
        <Row
          k="Credits"
          d="platform credits left. Spent per run, on this deployment's provider keys."
          value={
            loading ? 'reading…'
              : credits === null ? 'not metered on this deployment'
              : credits === 0 ? 'none left — the next run will be refused'
              : credits.toLocaleString()
          }
        />
      </div>
      <BuyCredits account={account} onBought={() => void read()} />

      <div className="settings-section"><CreditCard size={13} />Session</div>
      <div className="settings-card">
        <div className="settings-row">
          <div className="settings-label">
            <div className="k">Sign out</div>
            <div className="d">Revokes this window's session. Other windows keep theirs.</div>
          </div>
          <button className="btn" onClick={() => void account?.signOut?.()} disabled={!account?.signOut}>
            Sign out
          </button>
        </div>
        <div className="settings-row">
          <div className="settings-label">
            <div className="k">Refresh balance</div>
            <div className="d">Also happens automatically whenever a run finishes.</div>
          </div>
          <button className="btn" onClick={() => void read()}><RefreshCw size={14} />Refresh</button>
        </div>
      </div>
    </>
  )
}

/**
 * Buying credits, from the window itself.
 *
 * `GET /products?kind=credit_pack` is PUBLIC (a catalogue has to be browsable) and `/me/purchase`
 * takes nothing but a product id — price and credit count are read from the products row, never
 * from the request, so a client cannot post itself a fortune. Both are things this page may call
 * without any of it passing through the daemon.
 *
 * THE RAIL DESCRIBES ITSELF and this renders what it says. On a deployment running the null
 * gateway a purchase grants credits without charging anything, and the honest thing is to print
 * the gateway's own note rather than draw a checkout that implies otherwise.
 */
function BuyCredits({
  account,
  onBought
}: {
  account?: AccountAdapter
  onBought(): void
}): JSX.Element | null {
  const [packs, setPacks] = useState<CreditPack[]>([])
  const [provider, setProvider] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [said, setSaid] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    if (!account?.products) return
    void account
      .products()
      .then((r) => {
        if (!alive) return
        setPacks(r.products || [])
        setProvider(r.provider || '')
        setNote(r.note || '')
      })
      .catch(() => { if (alive) setPacks([]) })
    return () => { alive = false }
  }, [account])

  if (!account?.products || !account.buy) return null
  if (!packs.length) return null

  async function buy(pack: CreditPack): Promise<void> {
    setBusy(pack.id)
    setError('')
    setSaid('')
    try {
      const r = await account!.buy!(pack.id)
      setSaid(
        `Added ${r.credits.toLocaleString()} credits — ${r.creditsRemaining.toLocaleString()} now available.` +
          (r.detail ? ` ${r.detail}` : '')
      )
      onBought()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy('')
    }
  }

  return (
    <>
      <div className="settings-section"><CreditCard size={13} />Buy credits</div>
      <div className="settings-card">
        {packs.map((pack) => (
          <div className="settings-row" key={pack.id}>
            <div className="settings-label">
              <div className="k">{pack.title || pack.name || pack.id}</div>
              <div className="d">
                {(pack.credits || 0).toLocaleString()} credits
                {pack.description ? ` · ${pack.description}` : ''}
              </div>
            </div>
            <button className="btn" disabled={!!busy} onClick={() => void buy(pack)}>
              {busy === pack.id ? 'buying…' : `$${Number(pack.price_usd || 0).toFixed(2)}`}
            </button>
          </div>
        ))}
      </div>
      {said && <p className="settings-note">{said}</p>}
      {error && <p className="settings-note error">{error}</p>}
      {note && (
        <p className="settings-note">
          {note}
          {provider ? ` (payment rail: ${provider})` : ''}
        </p>
      )}
    </>
  )
}

function ModelsTab({ config, plugins }: { config: AgentConfig | null; plugins: CatalogPlugin[] }): JSX.Element {
  // Every tool that actually runs on a model, with the model it resolves to TODAY — the answer to
  // "what is this agent using", which no single config value can give: an agent's own agent.toml
  // overrides the global config per tool, and the resolver is the only thing that knows the winner.
  const modelTools = plugins
    .flatMap((p) => (p.tools || []).map((t) => ({ plugin: p.id, ...t })))
    .filter((t) => t.needsModel && t.model)
    .sort((a, b) => a.name.localeCompare(b.name))

  const fallbacks = Array.isArray(config?.model_fallbacks) ? (config?.model_fallbacks as string[]) : []

  return (
    <>
      <div className="settings-section"><Cpu size={13} />The agent's brain</div>
      <div className="settings-card">
        <Row k="Model" d="what answers you in chat and decides which tools to run" value={config?.model || '—'} />
        <Row k="Reasoning effort" value={String(config?.reasoning_effort || 'default')} />
        {fallbacks.length > 0 && (
          <Row k="Fallbacks" d="tried in order when the model above cannot serve a turn" value={fallbacks.join(' → ')} />
        )}
      </div>

      <div className="settings-section"><Cpu size={13} />Models the tools run on</div>
      <div className="kv-card">
        {modelTools.length === 0 && <div className="kv-row"><span className="kv-val">no model-bearing tools installed</span></div>}
        {modelTools.map((t) => (
          <div className="kv-row" key={`${t.plugin}.${t.name}`}>
            <span className="kv-key">{t.name}</span>
            <span className="kv-val">
              {t.model}
              {t.provider ? `  ·  ${t.provider}` : ''}
              {t.modelKind && t.modelKind !== 'text' ? `  ·  ${t.modelKind}` : ''}
            </span>
          </div>
        ))}
      </div>
      <p className="settings-note">
        Read-only here. On a hosted deployment this configuration belongs to the server, not to one
        account — changing it would change it for everyone signed in. Run the agent on your own
        machine to edit it.
      </p>
    </>
  )
}

function ToolsTab({ plugins }: { plugins: CatalogPlugin[] }): JSX.Element {
  const [open, setOpen] = useState<string>('')
  return (
    <>
      <div className="settings-section"><Boxes size={13} />Plugins and their tools</div>
      {plugins.length === 0 && <p className="settings-note">The catalog came back empty.</p>}
      {plugins.map((p) => {
        const tools = p.tools || []
        const on = tools.filter((t) => t.enabled !== false).length
        const expanded = open === p.id
        return (
          <div className="settings-card" key={p.id}>
            <div className="settings-row" role="button" tabIndex={0} onClick={() => setOpen(expanded ? '' : p.id)}>
              <div className="settings-label">
                <div className="k">{p.id}</div>
                <div className="d">{p.description || 'no description'}</div>
              </div>
              <div className="kv-val">
                {p.enabled === false ? 'disabled' : `${on}/${tools.length} tools`}
              </div>
            </div>
            {expanded &&
              tools.map((t) => (
                <div className="settings-row" key={t.name}>
                  <div className="settings-label">
                    <div className="k"><Wrench size={12} /> {t.name}</div>
                    <div className="d">{(t.description || '').split('\n')[0].slice(0, 160)}</div>
                  </div>
                  <div className="kv-val">
                    {t.enabled === false ? 'off' : t.model ? t.model : 'on'}
                  </div>
                </div>
              ))}
          </div>
        )
      })}
    </>
  )
}

/**
 * The settings surface. Rendered in the MAIN column (not a modal) so it behaves like the shell's
 * own settings page: the sidebar stays, the conversation is one click behind you.
 */
export function SettingsPage({
  client,
  account,
  agentId,
  title,
  onClose
}: {
  client: SettingsClient
  account?: AccountAdapter
  agentId?: string
  title: string
  onClose(): void
}): JSX.Element {
  const [tab, setTab] = useState<Tab>('account')
  const [plugins, setPlugins] = useState<CatalogPlugin[]>([])
  const [config, setConfig] = useState<AgentConfig | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const cat = await client.request<{ plugins: CatalogPlugin[] }>('plugins.catalog')
        if (alive) setPlugins(cat.plugins || [])
      } catch (e) {
        if (alive) setError(String((e as Error)?.message || e))
      }
      try {
        // agentId so a daemon serving many agents answers for THIS one; the gateway forces it to
        // the connection's own agent anyway, which is what makes the call safe to expose at all.
        // `values` — the payload's own name for the effective value of every exposed knob
        // (gateway._config_get). Not `config`: that guess rendered an em-dash where the model
        // should be, which is exactly the sort of wrong-but-plausible a settings page must not do.
        const cfg = await client.request<{ values: AgentConfig }>('config.get', agentId ? { agentId } : {})
        if (alive) setConfig(cfg.values || null)
      } catch {
        /* an older daemon, or one that does not expose config — the other tabs still work */
      }
    })()
    return () => { alive = false }
  }, [client, agentId])

  return (
    <div className="settings">
      <div className="entity-head">
        <div className="settings-inner">
          <div className="settings-head">
            <div className="settings-head-titles">
              <h1 className="page-title">Settings</h1>
              <div className="page-sub">{title}</div>
            </div>
            <div className="settings-head-actions">
              <button className="cv-btn" title="close (back to the chat)" onClick={onClose}><X size={16} /></button>
            </div>
          </div>
          <div className="entity-tabbar">
            <div className="seg entity-tabs">
              <button className={tab === 'account' ? 'on' : ''} onClick={() => setTab('account')}><User size={14} />Account</button>
              <button className={tab === 'models' ? 'on' : ''} onClick={() => setTab('models')}><Cpu size={14} />Models</button>
              <button className={tab === 'tools' ? 'on' : ''} onClick={() => setTab('tools')}><Boxes size={14} />Tools &amp; plugins</button>
            </div>
          </div>
        </div>
      </div>
      <div className="settings-body">
        <div className="settings-inner">
          {error && <div className="banner banner-error">{error}</div>}
          {tab === 'account' && <AccountTab account={account} />}
          {tab === 'models' && <ModelsTab config={config} plugins={plugins} />}
          {tab === 'tools' && <ToolsTab plugins={plugins} />}
        </div>
      </div>
    </div>
  )
}
