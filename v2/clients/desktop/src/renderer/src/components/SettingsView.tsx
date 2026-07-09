import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, RotateCcw, Save, ShieldCheck, Loader2, AlertCircle, X, Cpu, Eye, EyeOff, Lock, Plus, Trash2, Plug, ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react'

import { gateway } from '../gateway/client'
import {
  SETTINGS_TABS,
  bareKey,
  fieldScope,
  type FieldDef,
  type GroupDef
} from '../lib/settingsSchema'
import { useApp } from '../state/store'
import PageShell from './PageShell'
import SearchBox from './SearchBox'

/** does a field match a search query (matched on its label + bare key) */
function fieldMatches(f: FieldDef, ql: string): boolean {
  return !ql || `${f.label} ${bareKey(f.key)}`.toLowerCase().includes(ql)
}

type ModelOption = { value: string; label: string; group?: string; provider?: string }

/** The daemon's editable-config surface (config.get response). */
interface ConfigData {
  path: string
  exists: boolean
  envPath: string
  values: Record<string, any>
  env: Record<string, boolean>
  envValues: Record<string, string>
  /** {config_key: AGENTD_VAR} — knobs an env var currently pins (config.json can't win) */
  envOverrides: Record<string, string>
  providerKeys: string[]
  catalogs: Record<string, ModelOption[]>
  raw: string
  effectiveModel: string
  version: string
}

// Config keys that take effect on the NEXT message (read per-prompt) — everything else is
// read at daemon boot, so saving it triggers an auto-restart to apply.
const LIVE_KEYS = new Set(['agent_name', 'completeness_check'])

// ---- client-only preferences (instant, localStorage) ------------------------------
const NOTIFY_KEY = 'agentd-notifications'
const readClient = (key: string): string => {
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}
const writeClient = (key: string, value: string): void => {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable */
  }
}

// ---- nested value helpers (dotted keys like cost_efficiency.enabled) ---------------
function getPath(obj: Record<string, any>, path: string): any {
  return path.split('.').reduce<any>((acc, k) => (acc == null ? undefined : acc[k]), obj)
}
function setPath(obj: Record<string, any>, path: string, value: any): Record<string, any> {
  const keys = path.split('.')
  const root = { ...obj }
  let cursor: Record<string, any> = root
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]
    cursor[k] = cursor[k] && typeof cursor[k] === 'object' ? { ...cursor[k] } : {}
    cursor = cursor[k]
  }
  cursor[keys[keys.length - 1]] = value
  return root
}

/** The AGENTD_* env var pinning a config field (so it's read-only in the UI), or '' if none.
 *  Only config-scope knobs can be env-pinned; nesting maps to its top-level config key. */
function envVarFor(data: ConfigData | null, key: string): string {
  if (fieldScope(key) !== 'config') return ''
  return data?.envOverrides?.[key.split('.')[0]] || ''
}

/** the shared render context passed down to GroupCard / FieldRow */
interface Ctx {
  draft: Record<string, any>
  data: ConfigData | null
  keysDraft: Record<string, string>
  listBuf: Record<string, string>
  setListBuf: (u: (b: Record<string, string>) => Record<string, string>) => void
  setDraft: (u: (d: Record<string, any>) => Record<string, any>) => void
  valueOf: (f: FieldDef) => any
  setValue: (f: FieldDef, v: any) => void
}

export default function SettingsView() {
  const flavor = useApp((s) => s.flavor)
  const hello = useApp((s) => s.hello)
  const supervisor = useApp((s) => s.supervisor)
  const connection = useApp((s) => s.connection)
  const theme = useApp((s) => s.theme)
  const toggleTheme = useApp((s) => s.toggleTheme)

  const [tab, setTab] = useState<string>('general')
  const [search, setSearch] = useState('')
  const [data, setData] = useState<ConfigData | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [keysDraft, setKeysDraft] = useState<Record<string, string>>({})
  const [listBuf, setListBuf] = useState<Record<string, string>>({})
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')

  // client prefs (instant)
  const [notify, setNotify] = useState(() => readClient(NOTIFY_KEY) !== '0')

  const load = useCallback(async () => {
    try {
      const res = (await gateway.request('config.get')) as ConfigData
      setData(res)
      setDraft({ ...res.values })
      // prefill secret fields with the saved values so a key shows up (masked) with a reveal toggle
      setKeysDraft({ ...(res.envValues || {}) })
      setListBuf({})
      setLoadError('')
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    if (connection === 'open') void load()
  }, [connection, load])

  // ---- read/write a single field's value --------------------------------------
  const valueOf = (field: FieldDef): any => {
    const scope = fieldScope(field.key)
    if (scope === 'client') {
      const k = bareKey(field.key)
      if (k === 'theme') return theme
      if (k === 'notifications') return notify
      return ''
    }
    if (scope === 'env') return keysDraft[bareKey(field.key)] ?? ''
    return getPath(draft, field.key)
  }

  const setValue = (field: FieldDef, value: any): void => {
    setNote('')
    const scope = fieldScope(field.key)
    if (scope === 'client') {
      const k = bareKey(field.key)
      if (k === 'theme') {
        if (value !== theme) toggleTheme()
      } else if (k === 'notifications') {
        setNotify(Boolean(value))
        writeClient(NOTIFY_KEY, value ? '1' : '0')
      }
      return
    }
    if (scope === 'env') {
      setKeysDraft((d) => ({ ...d, [bareKey(field.key)]: String(value) }))
      return
    }
    setDraft((d) => setPath(d, field.key, value))
  }

  // ---- dirty tracking + save --------------------------------------------------
  const patch = useMemo(() => {
    if (!data) return {}
    const out: Record<string, any> = {}
    for (const key of Object.keys(draft)) {
      if (draft[key] === '' && typeof data.values[key] === 'number') continue
      if (JSON.stringify(draft[key]) !== JSON.stringify(data.values[key])) out[key] = draft[key]
    }
    return out
  }, [draft, data])

  // secret fields are PREFILLED with the saved value (envValues), so only send the ones the user
  // actually CHANGED — a cleared field ('') deletes the key on the backend, an edit rewrites it.
  const keys = useMemo(() => {
    const orig = data?.envValues || {}
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(keysDraft)) {
      if (v !== (orig[k] ?? '')) out[k] = v
    }
    return out
  }, [keysDraft, data])
  const changeCount = Object.keys(patch).length + Object.keys(keys).length
  const dirty = changeCount > 0

  const save = async (): Promise<void> => {
    if (!dirty || saving) return
    setSaving(true)
    setNote('')
    try {
      const res = (await gateway.request('config.set', { patch, keys })) as {
        saved: boolean
        error?: string
      }
      if (!res.saved) throw new Error(res.error || 'save failed')
      // Most config knobs are read when the daemon BOOTS, so they only take effect after a
      // restart. If the save touched any such key, restart the daemon so the change actually
      // applies (API keys + the LIVE_KEYS below are already live and skip the restart).
      const needsRestart = Object.keys(patch).some((k) => !LIVE_KEYS.has(k))
      if (needsRestart && window.agentd?.restartDaemon) {
        setData((d) => (d ? { ...d, values: { ...d.values, ...patch } } : d)) // optimistically clean
        setKeysDraft({})
        setListBuf({})
        setNote('Saved — restarting the agent to apply…')
        try {
          await window.agentd.restartDaemon()
          setNote('Applied — agent restarted.')
        } catch {
          setNote('Saved, but the restart failed — restart the daemon manually to apply.')
        }
        // the connection-open effect reloads the fresh config once the gateway reconnects
      } else {
        await load()
        setNote(needsRestart ? 'Saved — restart the daemon to apply.' : 'Saved.')
      }
    } catch (e) {
      setNote(`Couldn’t save: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const reset = (): void => {
    if (!data) return
    setDraft({ ...data.values })
    setKeysDraft({ ...(data.envValues || {}) })
    setListBuf({})
    setNote('')
  }

  const active = SETTINGS_TABS.find((t) => t.id === tab) || SETTINGS_TABS[0]
  const ctx: Ctx = { draft, data, keysDraft, listBuf, setListBuf, setDraft, valueOf, setValue }

  const sub = data ? (
    <>Configuring <code>{data.path}</code></>
  ) : loadError ? (
    <span className="danger-text">Couldn’t load config: {loadError}</span>
  ) : (
    'Loading configuration…'
  )

  const actions = (dirty || note) ? (
    <>
      <span className={`settings-savenote ${note.startsWith('Couldn') ? 'err' : ''}`}>
        {note ? (
          note.startsWith('Couldn') ? <AlertCircle size={14} /> : <Check size={14} />
        ) : (
          <ShieldCheck size={14} />
        )}
        {note || `${changeCount} unsaved change${changeCount === 1 ? '' : 's'}`}
      </span>
      {dirty && (
        <button className="btn ghost" onClick={reset} disabled={saving}>
          <RotateCcw size={14} />Reset
        </button>
      )}
      <button className="btn primary" onClick={() => void save()} disabled={!dirty || saving}>
        {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
        {saving ? 'Saving…' : 'Save changes'}
      </button>
    </>
  ) : null

  const nav = SETTINGS_TABS.map((t) => {
    const Icon = t.icon
    return (
      <button
        key={t.id}
        className={`settings-nav-item ${t.id === tab ? 'active' : ''}`}
        onClick={() => { setTab(t.id); setSearch('') }}
      >
        <Icon size={16} />
        <span>{t.label}</span>
      </button>
    )
  })

  // Only the tabs with a lot to sift through carry a (pinned) search: the tool catalog and the
  // provider-key list. It lives in the PageShell scaffold, so it never scrolls away.
  const searchable = active.custom === 'tools' || active.id === 'keys'
  const searchNode = searchable ? (
    <SearchBox
      value={search}
      onChange={setSearch}
      placeholder={active.custom === 'tools' ? 'Search tools & plugins…' : 'Search keys…'}
    />
  ) : null

  const ql = search.trim().toLowerCase()
  const keysEmpty =
    active.id === 'keys' && ql !== '' && !active.groups.some((g) => g.fields.some((f) => fieldMatches(f, ql)))

  return (
    <PageShell title="Settings" sub={sub} actions={actions} nav={nav} search={searchNode}>
      {active.custom === 'runtime' ? (
        <RuntimeTab
          ctx={ctx}
          flavor={flavor}
          hello={hello}
          supervisor={supervisor}
          connection={connection}
          groups={active.groups}
          onReload={load}
          onNote={setNote}
        />
      ) : (
        <>
          {active.custom === 'models' && <ModelsPanel ctx={ctx} />}
          {active.custom === 'tools' && <ToolsAndPlugins ctx={ctx} query={search} onEdit={() => setNote('')} />}
          {keysEmpty && <div className="settings-empty">No keys match “{search.trim()}”.</div>}
          {active.groups.map((group) => (
            <GroupCard key={group.title} group={group} ctx={ctx} query={active.id === 'keys' ? search : ''} />
          ))}
        </>
      )}
    </PageShell>
  )
}

// ---- a group of fields as one card (shared by every tab) --------------------------
// `query` (when the tab has a search bar) narrows the visible fields to those matching it.
function GroupCard({ group, ctx, query = '' }: { group: GroupDef; ctx: Ctx; query?: string }) {
  const ql = query.trim().toLowerCase()
  const fields = group.fields.filter((f) => {
    if (f.showWhen && getPath(ctx.draft, f.showWhen.key) !== f.showWhen.equals) return false
    return fieldMatches(f, ql)
  })
  if (fields.length === 0) return null
  const optionsFor = (f: FieldDef): ModelOption[] =>
    f.optionsKey ? ctx.data?.catalogs?.[f.optionsKey] || [] : f.options || []

  return (
    <div className="settings-group">
      <div className="settings-section">{group.title}</div>
      {group.help && <p className="settings-help">{group.help}</p>}
      <div className="settings-card">
        {fields.map((field) => (
          <FieldRow
            key={field.key}
            field={field}
            value={ctx.valueOf(field)}
            options={optionsFor(field)}
            envSet={fieldScope(field.key) === 'env' && !!ctx.data?.env[bareKey(field.key)]}
            envTouched={fieldScope(field.key) === 'env' && (ctx.keysDraft[bareKey(field.key)] ?? '') !== (ctx.data?.envValues?.[bareKey(field.key)] ?? '')}
            pinnedBy={envVarFor(ctx.data, field.key)}
            listBuf={ctx.listBuf}
            setListBuf={ctx.setListBuf}
            onChange={(v) => ctx.setValue(field, v)}
          />
        ))}
      </div>
    </div>
  )
}

// ---- Models tab: cost-efficiency toggle first, then the model(s) that actually run ----
// Off -> one "Brain" (drives every turn).
// On  -> "General brain" (text turns) + "Brain with vision" (image turns).
function ModelsPanel({ ctx }: { ctx: Ctx }) {
  const models: ModelOption[] = ctx.data?.catalogs?.models || []
  const ceRaw = ctx.draft.cost_efficiency
  const ce = ceRaw && typeof ceRaw === 'object' ? ceRaw : {}
  const enabled = !!ce.enabled

  const field = (key: string, label: string, help: string): FieldDef =>
    ({ key, label, help, type: 'select', optionsKey: 'models' })
  const render = (f: FieldDef) => (
    <FieldRow
      key={f.key}
      field={f}
      value={ctx.valueOf(f)}
      options={models}
      envSet={false}
      envTouched={false}
      pinnedBy={envVarFor(ctx.data, f.key)}
      listBuf={ctx.listBuf}
      setListBuf={ctx.setListBuf}
      onChange={(v) => ctx.setValue(f, v)}
    />
  )

  return (
    <div className="settings-group">
      <div className="settings-section">Models</div>
      <p className="settings-help">
        Only models for providers you have an API key for are listed. Add a key under{' '}
        <b>API Keys</b> to unlock more. Selecting a model just needs its provider key to work.
      </p>
      <div className="settings-card">
        {render({
          key: 'cost_efficiency.enabled',
          label: 'Cost efficiency',
          help: 'Run a cheaper model on ordinary text turns and only switch to a stronger model when a turn actually involves an image.',
          type: 'toggle'
        })}
        <div className="model-rec">
          <Cpu size={15} />
          <span>
            <b>Recommended.</b> Same quality on everyday tasks at a fraction of the cost — a text model
            like DeepSeek runs roughly <b>10× cheaper</b> than a frontier vision model, which only kicks
            in when an image is actually in play.
          </span>
        </div>
        {!enabled && render(field('model', 'Brain', 'The model that powers every turn.'))}
        {enabled &&
          render(field('cost_efficiency.text_model', 'General brain', 'Runs ordinary text-only turns — the model you talk to most.'))}
        {enabled &&
          render(field('cost_efficiency.vision_model', 'Brain with vision capability', 'Runs turns that involve an image, and stays on once an image is in the chat.'))}
        {render({
          key: 'model_fallbacks',
          label: 'Failover models',
          help: 'Tried in order if the active model errors before producing any output. Optional.',
          type: 'modellist',
          optionsKey: 'models'
        })}
      </div>
      <ToolModels ctx={ctx} />
    </div>
  )
}

/** The "one place" for TOOL models — every model-bearing tool (image generation, vision reading,
 *  verify, …) with a KIND-CORRECT picker, from the same models.list the terminal reads. So an
 *  image tool only ever offers image models — no more picking a text model for artwork. */
function ToolModels({ ctx }: { ctx: Ctx }) {
  const [rows, setRows] = useState<any[] | null>(null)
  useEffect(() => {
    let alive = true
    gateway
      .request<{ models: any[] }>('models.list')
      .then((r) => alive && setRows((r.models || []).filter((m) => m.plugin)))
      .catch(() => alive && setRows([]))
    return () => {
      alive = false
    }
  }, [])
  if (!rows || rows.length === 0) return null
  return (
    <>
      <div className="settings-section" style={{ marginTop: 22 }}>Tool models</div>
      <p className="settings-help">
        The model each tool uses, from the one place that sees them all. An <b>image</b> tool offers
        image-generation models (Nano Banana, FLUX); a <b>vision</b> tool offers multimodal models —
        you can’t pick the wrong kind. For a backend-coupled model (image-gen) picking it also sets the
        tool’s <b>provider</b> automatically, so they can’t drift. Blank = the tool’s built-in default.
      </p>
      <div className="settings-card">
        {rows.map((m) => {
          const options: ModelOption[] = ctx.data?.catalogs?.[m.kind] || ctx.data?.catalogs?.models || []
          const val = getPath(ctx.draft, m.configKey) ?? m.resolved ?? ''
          const curProv = options.find((o) => o.value === val)?.provider
          return (
            <div className="settings-row" key={m.id}>
              <div className="settings-label">
                <div className="k">{m.label}{curProv ? ` · runs on ${curProv}` : ''}</div>
                <div className="d">{m.kind} · {m.plugin}</div>
              </div>
              <div className="settings-ctl">
                <select
                  className="settings-select"
                  value={val}
                  onChange={(e) => {
                    const v = e.target.value
                    const prov = options.find((o) => o.value === v)?.provider
                    // a backend-coupled model sets its provider too, so model + provider stay consistent
                    ctx.setDraft((d) => {
                      let next = setPath(d, m.configKey, v)
                      if (prov) next = setPath(next, m.configKey.replace(/\.model$/, '.provider'), prov)
                      return next
                    })
                  }}
                >
                  {optionEls(options, val)}
                </select>
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}

/** <select> options as flat list or provider optgroups, with the current value guaranteed present. */
function optionEls(options: ModelOption[], value?: string) {
  const groups = [...new Set(options.map((o) => o.group).filter(Boolean))] as string[]
  const missing = value && !options.some((o) => o.value === value)
  const lone = missing ? <option value={value}>{value}</option> : null
  if (groups.length === 0) {
    return (
      <>
        {lone}
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </>
    )
  }
  return (
    <>
      {lone}
      {groups.map((g) => (
        <optgroup key={g} label={g}>
          {options.filter((o) => o.group === g).map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </optgroup>
      ))}
    </>
  )
}

// ---- one config field, rendered by type -------------------------------------------
function FieldRow({
  field,
  value,
  options,
  envSet,
  envTouched,
  pinnedBy,
  listBuf,
  setListBuf,
  onChange
}: {
  field: FieldDef
  value: any
  options: ModelOption[]
  envSet: boolean
  envTouched: boolean
  /** AGENTD_* var pinning this knob in .env → render read-only (config.json can't win) */
  pinnedBy?: string
  listBuf: Record<string, string>
  setListBuf: (u: (b: Record<string, string>) => Record<string, string>) => void
  onChange: (v: any) => void
}) {
  const [shown, setShown] = useState(false) // secret reveal (eye) toggle
  const control = () => {
    switch (field.type) {
      case 'toggle':
        return <button className={`switch ${value ? 'on' : ''}`} onClick={() => onChange(!value)} />
      case 'segment':
        return (
          <div className="seg">
            {(options || []).map((o) => (
              <button key={o.value} className={value === o.value ? 'on' : ''} onClick={() => onChange(o.value)}>
                {o.label}
              </button>
            ))}
          </div>
        )
      case 'select':
        return (
          <select className="settings-select" value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
            {optionEls(options, value ?? '')}
          </select>
        )
      case 'modellist': {
        const arr: string[] = Array.isArray(value) ? value : []
        const labelOf = (v: string) => options.find((o) => o.value === v)?.label || v
        const remaining = options.filter((o) => !arr.includes(o.value))
        return (
          <div className="modellist">
            {arr.map((m) => (
              <span className="modellist-chip" key={m}>
                {labelOf(m)}
                <button title="remove" onClick={() => onChange(arr.filter((x) => x !== m))}>
                  <X size={12} />
                </button>
              </span>
            ))}
            <select
              className="settings-select modellist-add"
              value=""
              onChange={(e) => {
                if (e.target.value) onChange([...arr, e.target.value])
              }}
            >
              <option value="">+ add fallback…</option>
              {optionEls(remaining)}
            </select>
          </div>
        )
      }
      case 'number':
        return (
          <div className="settings-num">
            <input
              type="number"
              value={value ?? ''}
              min={field.min}
              max={field.max}
              step={field.step}
              onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
            />
            {field.unit && <span className="settings-unit">{field.unit}</span>}
          </div>
        )
      case 'secret': {
        const filled = (value ?? '') !== ''
        return (
          <div className={`secret-input ${filled ? 'has-eye' : ''}`}>
            <input
              type={shown ? 'text' : 'password'}
              className="settings-input"
              autoComplete="off"
              spellCheck={false}
              value={value ?? ''}
              placeholder={field.placeholder || 'Paste a key to add'}
              onChange={(e) => onChange(e.target.value)}
            />
            {/* eye only when there's something to reveal (a saved/typed key) */}
            {filled && (
              <button
                type="button"
                className="secret-eye"
                title={shown ? 'Hide key' : 'Show key'}
                aria-label={shown ? 'Hide key' : 'Show key'}
                onClick={() => setShown((s) => !s)}
              >
                {shown ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            )}
          </div>
        )
      }
      case 'list': {
        const text = listBuf[field.key] !== undefined ? listBuf[field.key] : (Array.isArray(value) ? value.join('\n') : '')
        return (
          <textarea
            className="settings-input settings-area"
            rows={Math.max(2, text.split('\n').length)}
            value={text}
            placeholder={field.placeholder}
            spellCheck={false}
            onChange={(e) => {
              const t = e.target.value
              setListBuf((b) => ({ ...b, [field.key]: t }))
              onChange(t.split('\n').map((s) => s.trim()).filter(Boolean))
            }}
          />
        )
      }
      default:
        return (
          <input
            className="settings-input"
            type="text"
            value={value ?? ''}
            placeholder={field.placeholder}
            spellCheck={false}
            onChange={(e) => onChange(e.target.value)}
          />
        )
    }
  }

  const stacked = field.type === 'list' || field.type === 'secret' || field.type === 'text' || field.type === 'modellist'
  return (
    <div className={`settings-row ${stacked ? 'settings-row--stacked' : ''} ${pinnedBy ? 'settings-row--pinned' : ''}`}>
      <div className="settings-label">
        <div className="k">
          {field.label}
          {field.type === 'secret' && (
            <span className={`key-chip ${envTouched ? 'edit' : envSet ? 'ok' : ''}`}>
              {envTouched ? 'will save' : envSet ? 'set' : 'not set'}
            </span>
          )}
          {pinnedBy && (
            <span className="pinned-lock" title={`Locked — set by ${pinnedBy} in .env (remove it there to edit here)`}>
              <Lock size={12} />
            </span>
          )}
        </div>
        {field.help && <div className="d">{field.help}</div>}
      </div>
      {/* pinned knobs are non-interactive: config.json (what a save writes) can't beat the env var */}
      <div className="settings-ctl">
        {pinnedBy ? <div className="pinned-ctl">{control()}</div> : control()}
      </div>
    </div>
  )
}

// ---- Tools & plugins tab: one catalog, grouped by plugin ---------------------------
// Each plugin is a card: a master on/off plus its tools nested, each with its own on/off and
// (where a tool takes a model) a model dropdown. Backed by plugins.catalog; edits write
// tools_disabled (per-tool), plugins[id].enabled (plugin gate) and plugins[id].tools[t].model.
interface CatTool {
  name: string
  /** short display name (MCP tools strip the `server__` prefix) */
  label?: string
  description: string
  needsModel: boolean
  /** which model picker to show: text | vision | image | embedding */
  modelKind?: string
  model: string | null
  provider: string | string[] | null
  /** self-described valid provider values → render a picker instead of a text box (empty ⇒ free text) */
  providerOptions?: string[]
  /** provider is an ORDERED chain (multi-select) vs a single pick */
  providerChain?: boolean
  enabled: boolean
}

/** An ordered provider CHAIN picker (e.g. web_search): removable chips + an add-dropdown, written
 *  back as an array. Reuses the modellist styling. Empty ⇒ the tool's own default order. */
function ProviderChain({ options, value, onChange }: { options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  const remaining = options.filter((o) => !value.includes(o))
  return (
    <div className="modellist">
      {value.map((m) => (
        <span className="modellist-chip" key={m}>
          {m}
          <button title="remove" onClick={() => onChange(value.filter((x) => x !== m))}>
            <X size={12} />
          </button>
        </span>
      ))}
      <select
        className="settings-select modellist-add"
        value=""
        onChange={(e) => { if (e.target.value) onChange([...value, e.target.value]) }}
      >
        <option value="">{value.length ? '+ add…' : 'auto (default order)'}</option>
        {remaining.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}
interface CatPlugin {
  id: string
  description: string
  enabled: boolean
  tools: CatTool[]
  /** an MCP server (data source) rather than a native plugin — rendered with an endpoint + remove */
  mcp?: boolean
  endpoint?: string
  transport?: string
}

/** A description as ONE truncated line; clicking opens the full text in the Canvas. Shared by
 *  plugins and tools so both read the same way (no per-element styling). */
function DescLine({ text, title, onOpen }: { text: string; title: string; onOpen: () => void }) {
  return (
    <button className="desc-line" title={`Open ${title} — full description`} onClick={onOpen}>
      {text.replace(/\s+/g, ' ').trim()}
    </button>
  )
}

function ToolsAndPlugins({ ctx, query, onEdit }: { ctx: Ctx; query: string; onEdit: () => void }) {
  const openCanvas = useApp((s) => s.openCanvas)
  const [cat, setCat] = useState<CatPlugin[] | null>(null)
  const [err, setErr] = useState('')
  // add-an-MCP form — the SAME mcp.add RPC the Data sources page uses (connects live, no restart)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [form, setForm] = useState({ name: '', kind: 'stdio' as 'stdio' | 'http', command: '', url: '' })
  // clean split: native plugins vs MCP servers; collapse set (by plugin id) so long lists stay readable
  const [subtab, setSubtab] = useState<'plugins' | 'mcp'>('plugins')
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())

  // open a description (in-memory markdown) in the Canvas — the same surface skills use
  const openDesc = (kind: 'plugin' | 'tool', id: string, description: string): void =>
    openCanvas({ path: `doc:${kind}:${id}`, name: `${id} · ${kind}`, mime: 'text/markdown', kind: 'file', text: description })

  const reload = useCallback(() => {
    gateway
      .request<{ plugins: CatPlugin[] }>('plugins.catalog')
      .then((r) => setCat(r.plugins || []))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [])
  useEffect(() => reload(), [reload])

  const canAdd = form.name.trim() && (form.kind === 'http' ? form.url.trim() : form.command.trim())
  const addMcp = async (): Promise<void> => {
    if (!canAdd || busy) return
    setBusy(true); setNote('')
    try {
      const params: Record<string, unknown> = { name: form.name.trim() }
      if (form.kind === 'http') params.url = form.url.trim()
      else params.command = form.command.trim().split(/\s+/).filter(Boolean)
      const r = await gateway.request<{ added: boolean; error?: string }>('mcp.add', params)
      if (!r.added) throw new Error(r.error || 'could not connect')
      setNote(`Connected “${form.name.trim()}” — its tools are live.`)
      setAdding(false); setForm({ name: '', kind: 'stdio', command: '', url: '' }); reload()
    } catch (e) {
      setNote(`Couldn’t add: ${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(false) }
  }
  const removeMcp = async (name: string): Promise<void> => {
    try { await gateway.request('mcp.remove', { name }); setNote(`Removed “${name}”.`); reload() }
    catch (e) { setNote(`Couldn’t remove: ${e instanceof Error ? e.message : String(e)}`) }
  }

  const models = ctx.data?.catalogs?.models || []
  const disabled: string[] = Array.isArray(ctx.draft.tools_disabled) ? ctx.draft.tools_disabled : []
  const draftPlugins: Record<string, any> =
    ctx.draft.plugins && typeof ctx.draft.plugins === 'object' ? ctx.draft.plugins : {}

  const pluginOn = (id: string): boolean => {
    const v = draftPlugins[id]
    if (typeof v === 'boolean') return v
    if (v && typeof v === 'object') return v.enabled !== false
    return true
  }
  const setPluginOn = (id: string, on: boolean): void => {
    onEdit()
    ctx.setDraft((d) => {
      const plugins = { ...(d.plugins && typeof d.plugins === 'object' ? d.plugins : {}) }
      const cur = plugins[id]
      plugins[id] = typeof cur === 'boolean' || cur == null ? on : { ...cur, enabled: on }
      return { ...d, plugins }
    })
  }
  const setToolOn = (name: string, on: boolean): void => {
    onEdit()
    ctx.setDraft((d) => {
      const cur: string[] = Array.isArray(d.tools_disabled) ? d.tools_disabled : []
      const next = on ? cur.filter((n) => n !== name) : [...new Set([...cur, name])]
      return { ...d, tools_disabled: next }
    })
  }
  const setKnob = (pid: string, tool: string, knob: string, val: unknown): void => {
    onEdit()
    ctx.setDraft((d) => setPath(d, `plugins.${pid}.tools.${tool}.${knob}`, val))
  }
  // Picking a model also sets its backend PROVIDER when the model is backend-coupled (image-gen):
  // the catalog entry carries `provider`, so model + provider move together — ONE choice, never a
  // mismatch. For models with no provider (text/vision), only the model is written.
  const setModelKnob = (pid: string, tool: string, value: string, opts: ModelOption[]): void => {
    onEdit()
    const prov = opts.find((o) => o.value === value)?.provider
    ctx.setDraft((d) => {
      let next = setPath(d, `plugins.${pid}.tools.${tool}.model`, value)
      if (prov) next = setPath(next, `plugins.${pid}.tools.${tool}.provider`, prov)
      return next
    })
  }
  const knobValue = (pid: string, tool: string, knob: string): string =>
    getPath(ctx.draft, `plugins.${pid}.tools.${tool}.${knob}`) ?? ''

  const ql = query.trim().toLowerCase()
  const plugins = (cat || [])
    .map((p) => ({
      ...p,
      tools: ql
        ? p.tools.filter(
            (t) =>
              p.id.toLowerCase().includes(ql) ||
              t.name.toLowerCase().includes(ql) ||
              (t.description || '').toLowerCase().includes(ql)
          )
        : p.tools
    }))
    // keep any plugin with (matching) tools; ALSO keep MCP cards with no tools (not connected yet)
    // so every server stays visible — but still honour the search box.
    .filter((p) => p.tools.length > 0 || (p.mcp && (!ql || p.id.toLowerCase().includes(ql))))

  // Add-MCP is a full PAGE that takes over the tab (not a modal, not inline) — so it never pushes
  // the list down and can't be clipped by the scroll container.
  if (adding) {
    return (
      <div className="settings-group">
        <button className="btn ghost" onClick={() => { setAdding(false); setNote('') }}>
          <ArrowLeft size={14} />Tools &amp; plugins
        </button>
        <div className="settings-section" style={{ marginTop: 14 }}>Add MCP server</div>
        <p className="settings-help">
          Connect any MCP server — a shell <b>command (stdio)</b> or an <b>HTTP URL</b>. It connects
          live (no restart) and its tools join the catalog.
        </p>
        <form className="settings-card ds-form" onSubmit={(e) => { e.preventDefault(); void addMcp() }}>
          <label className="field">
            <span className="field-label">Name</span>
            <input className="field-input" autoFocus placeholder="e.g. gmail" value={form.name} spellCheck={false}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </label>
          <div className="field">
            <span className="field-label">Type</span>
            <div className="seg seg--start">
              <button type="button" className={form.kind === 'stdio' ? 'on' : ''} onClick={() => setForm((f) => ({ ...f, kind: 'stdio' }))}>Command (stdio)</button>
              <button type="button" className={form.kind === 'http' ? 'on' : ''} onClick={() => setForm((f) => ({ ...f, kind: 'http' }))}>URL (http)</button>
            </div>
          </div>
          {form.kind === 'stdio' ? (
            <label className="field">
              <span className="field-label">Command</span>
              <input className="field-input" placeholder="uvx some-mcp-server --flag" value={form.command} spellCheck={false}
                onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))} />
            </label>
          ) : (
            <label className="field">
              <span className="field-label">URL</span>
              <input className="field-input" placeholder="https://server.example.com/mcp" value={form.url} spellCheck={false}
                onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))} />
            </label>
          )}
          {note.startsWith('Couldn') && <div className="field-error">{note}</div>}
          <div className="ds-form-actions">
            <button type="button" className="btn ghost" onClick={() => { setAdding(false); setNote('') }} disabled={busy}>Cancel</button>
            <button type="submit" className="btn primary" disabled={!canAdd || busy}>
              {busy ? <Loader2 size={14} className="spin" /> : <Plus size={14} />}
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          </div>
        </form>
      </div>
    )
  }

  const shown = plugins.filter((p) => (subtab === 'mcp' ? !!p.mcp : !p.mcp))
  const allCollapsed = shown.length > 0 && shown.every((p) => collapsed.has(p.id))
  const toggleAll = (): void => setCollapsed(allCollapsed ? new Set() : new Set(shown.map((p) => p.id)))
  const toggleOne = (id: string): void =>
    setCollapsed((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  return (
    <div className="settings-group">
      <div className="tp-head">
        <div className="settings-section">Tools &amp; plugins</div>
        <div className="seg seg--start">
          <button className={subtab === 'plugins' ? 'on' : ''} onClick={() => setSubtab('plugins')}>Plugins</button>
          <button className={subtab === 'mcp' ? 'on' : ''} onClick={() => setSubtab('mcp')}>MCP servers</button>
        </div>
      </div>
      <div className="tp-toolbar">
        <button className="btn ghost" onClick={toggleAll} disabled={shown.length === 0}>
          {allCollapsed ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {allCollapsed ? 'Expand all' : 'Collapse all'}
        </button>
        {subtab === 'mcp' && (
          <button className="btn primary" onClick={() => { setAdding(true); setNote('') }}>
            <Plus size={14} />Add MCP server
          </button>
        )}
      </div>
      <p className="settings-help">
        {subtab === 'mcp'
          ? 'MCP servers (data sources) — each exposes its own tools. Add any server; it connects live (no restart). Per-tool on/off applies after a restart.'
          : 'Native plugins — toggle a whole plugin or a single tool, and set the model a tool uses. Changes apply after the daemon restarts.'}
      </p>
      {note && <div className={`ds-note ${note.startsWith('Couldn') ? 'err' : ''}`}>{note}</div>}
      {err && <div className="settings-empty danger-text">Couldn’t load: {err}</div>}
      {!cat && !err && <div className="settings-empty">Loading…</div>}
      {cat && shown.length === 0 && (
        <div className="settings-empty">
          {query.trim()
            ? `No matches for “${query.trim()}”.`
            : subtab === 'mcp'
              ? 'No MCP servers yet — add one above to connect external tools.'
              : 'No plugins.'}
        </div>
      )}

      {shown.map((p) => {
        const on = p.mcp ? p.enabled : pluginOn(p.id)
        const isCollapsed = collapsed.has(p.id)
        return (
          <div className={`plugin-card ${on ? '' : 'plugin-off'} ${isCollapsed ? 'plugin-collapsed' : ''}`} key={p.id}>
            <div className="plugin-head">
              <button className="plugin-collapse" title={isCollapsed ? 'expand' : 'collapse'} onClick={() => toggleOne(p.id)}>
                {isCollapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
              </button>
              <div className="plugin-headmain">
                <span className="plugin-name" onClick={() => toggleOne(p.id)}>
                  {p.mcp && <Plug size={13} className="mcp-ico" />}{p.id}
                  {p.mcp && <span className="key-chip">{p.enabled ? 'mcp · connected' : 'mcp'}</span>}
                  {isCollapsed && <span className="plugin-count">{p.tools.length} tool{p.tools.length === 1 ? '' : 's'}</span>}
                </span>
                {!isCollapsed && (p.mcp
                  ? (p.endpoint ? <span className="mcp-endpoint">{p.endpoint}</span> : null)
                  : (p.description && (
                      <DescLine text={p.description} title={p.id} onOpen={() => openDesc('plugin', p.id, p.description)} />
                    )))}
              </div>
              {p.mcp ? (
                <button className="hover-btn danger" title="remove MCP server" onClick={() => void removeMcp(p.id)}>
                  <Trash2 size={15} />
                </button>
              ) : (
                <button className={`switch ${on ? 'on' : ''}`} title="enable / disable plugin" onClick={() => setPluginOn(p.id, !on)} />
              )}
            </div>
            {!isCollapsed && p.tools.map((t) => {
              const toolOn = !disabled.includes(t.name)
              const modelVal = knobValue(p.id, t.name, 'model') || t.model || ''
              // offer the RIGHT kind of model: an image tool shows image models, not text ones
              const kindModels = ctx.data?.catalogs?.[t.modelKind || 'text'] || models
              // image-gen models are BACKEND-COUPLED (catalog entry carries `provider`): picking the
              // model sets the provider too, so we DON'T show a separate (mismatchable) provider control.
              const modelDrivesProvider = t.needsModel && kindModels.some((o) => o.provider)
              const modelProv = kindModels.find((o) => o.value === modelVal)?.provider
              const provOpts = t.providerOptions || []
              const hasProvider = !modelDrivesProvider && (provOpts.length > 0 || t.provider != null)
              const rawProv = knobValue(p.id, t.name, 'provider')
              // current provider value: draft override, else the resolved default (string | array)
              const provVal: string | string[] = rawProv !== '' ? rawProv : (t.provider ?? '')
              const provStr = typeof provVal === 'string' ? provVal : ''
              const provArr = Array.isArray(provVal) ? provVal : provVal ? [String(provVal)] : []
              return (
                <div className="pt-row" key={t.name}>
                  <div className="pt-info">
                    <div className="pt-name">{t.label || t.name}</div>
                    {t.description && (
                      <DescLine text={t.description} title={t.name} onOpen={() => openDesc('tool', t.name, t.description)} />
                    )}
                    {(t.needsModel || hasProvider) && (
                      <div className="pt-knobs">
                        {t.needsModel && (
                          <label className="pt-knob">
                            <span>model{modelDrivesProvider && modelProv ? ` · runs on ${modelProv}` : ''}</span>
                            <select className="settings-select" value={modelVal} onChange={(e) => setModelKnob(p.id, t.name, e.target.value, kindModels)}>
                              {optionEls(kindModels, modelVal)}
                            </select>
                          </label>
                        )}
                        {hasProvider && (
                          <label className="pt-knob">
                            <span>provider</span>
                            {provOpts.length === 0 ? (
                              // open-ended / unknown backend → free text (graceful fallback)
                              <input className="settings-input" value={provStr} spellCheck={false} onChange={(e) => setKnob(p.id, t.name, 'provider', e.target.value)} />
                            ) : t.providerChain ? (
                              // ordered chain (e.g. web_search) → chip multi-select, written as an array
                              <ProviderChain options={provOpts} value={provArr} onChange={(arr) => setKnob(p.id, t.name, 'provider', arr)} />
                            ) : (
                              // single pick (browser engine, image backend) → dropdown
                              <select className="settings-select" value={provStr} onChange={(e) => setKnob(p.id, t.name, 'provider', e.target.value)}>
                                <option value="">(default)</option>
                                {provStr && !provOpts.includes(provStr) && <option value={provStr}>{provStr}</option>}
                                {provOpts.map((o) => <option key={o} value={o}>{o}</option>)}
                              </select>
                            )}
                          </label>
                        )}
                      </div>
                    )}
                  </div>
                  <button className={`switch ${toolOn ? 'on' : ''}`} title="enable / disable tool" onClick={() => setToolOn(t.name, !toolOn)} />
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

// ---- Runtime tab: live status + server/limit fields + raw-config editor -----------
function RuntimeTab({
  ctx,
  flavor,
  hello,
  supervisor,
  connection,
  groups,
  onReload,
  onNote
}: {
  ctx: Ctx
  flavor: any
  hello: any
  supervisor: any
  connection: string
  groups: GroupDef[]
  onReload: () => Promise<void>
  onNote: (s: string) => void
}) {
  const data = ctx.data
  const [raw, setRaw] = useState('')
  const [rawSaving, setRawSaving] = useState(false)
  const [rawErr, setRawErr] = useState('')
  useEffect(() => {
    if (data) setRaw(data.raw)
  }, [data])

  const rows: [string, string][] = [
    ['Product', `${flavor?.productName || 'agentd'} · shell ${flavor?.version || '?'}`],
    ['Daemon', supervisor.message],
    ['Connection', connection],
    ['Gateway', hello?.version || '—'],
    ['Effective model', data?.effectiveModel || hello?.model || '—'],
    ['Workspace', hello?.workspace || '—'],
    ['Config file', data?.path || '—'],
    ['Secrets file', data?.envPath || '—'],
    ['Registry', hello?.registryUrl || `local · ${hello?.localRegistryDir || '?'}`]
  ]

  const saveRaw = async (): Promise<void> => {
    setRawSaving(true)
    setRawErr('')
    try {
      const res = (await gateway.request('config.set', { raw })) as { saved: boolean; error?: string }
      if (!res.saved) throw new Error(res.error || 'save failed')
      await onReload()
      onNote('Saved raw config — restart the daemon to fully apply.')
    } catch (e) {
      setRawErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRawSaving(false)
    }
  }

  return (
    <>
      <div className="settings-group">
        <div className="settings-section">Status</div>
        <div className="kv-card">
          {rows.map(([k, v]) => (
            <div className="kv-row" key={k}>
              <span className="kv-key">{k}</span>
              <span className="kv-val">{v}</span>
            </div>
          ))}
        </div>
      </div>

      {groups.map((group) => (
        <GroupCard key={group.title} group={group} ctx={ctx} />
      ))}

      <div className="settings-group">
        <div className="settings-section">Advanced — raw config</div>
        <p className="settings-help">
          The full <code>agentd.config.json</code>. Editing here overwrites the file wholesale — a
          safety net for knobs not surfaced above. Saving needs a daemon restart to apply.
        </p>
        <textarea
          className="settings-input settings-area settings-raw"
          rows={16}
          value={raw}
          spellCheck={false}
          onChange={(e) => setRaw(e.target.value)}
        />
        {rawErr && <div className="field-error mt-8">{rawErr}</div>}
        <div className="row-end">
          <button className="btn primary" onClick={() => void saveRaw()} disabled={rawSaving || raw === data?.raw}>
            {rawSaving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
            {rawSaving ? 'Saving…' : 'Save raw config'}
          </button>
        </div>
      </div>
    </>
  )
}
