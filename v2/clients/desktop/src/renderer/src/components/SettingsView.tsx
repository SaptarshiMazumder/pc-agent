import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, RotateCcw, Save, ShieldCheck, Loader2, AlertCircle, Search, X, Cpu } from 'lucide-react'

import { gateway } from '../gateway/client'
import {
  SETTINGS_TABS,
  bareKey,
  fieldScope,
  type FieldDef,
  type GroupDef
} from '../lib/settingsSchema'
import { useApp } from '../state/store'

type ModelOption = { value: string; label: string; group?: string }

/** The daemon's editable-config surface (config.get response). */
interface ConfigData {
  path: string
  exists: boolean
  envPath: string
  values: Record<string, any>
  env: Record<string, boolean>
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
      setKeysDraft({})
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

  // only non-empty secrets are sent — a blank field means "keep the current value"
  const keys = useMemo(
    () => Object.fromEntries(Object.entries(keysDraft).filter(([, v]) => v.trim() !== '')),
    [keysDraft]
  )
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
    setKeysDraft({})
    setListBuf({})
    setNote('')
  }

  const active = SETTINGS_TABS.find((t) => t.id === tab) || SETTINGS_TABS[0]
  const ctx: Ctx = { draft, data, keysDraft, listBuf, setListBuf, setDraft, valueOf, setValue }

  return (
    <div className="settings">
      <div className="settings-inner settings-wide">
        <div className="settings-head">
          <div className="settings-head-titles">
            <div className="page-title">Settings</div>
            <div className="page-sub">
              {data ? (
                <>Configuring <code>{data.path}</code></>
              ) : loadError ? (
                <span style={{ color: 'var(--danger)' }}>Couldn’t load config: {loadError}</span>
              ) : (
                'Loading configuration…'
              )}
            </div>
          </div>
          {(dirty || note) && (
            <div className="settings-head-actions">
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
            </div>
          )}
        </div>

        <div className="settings-layout">
          {/* left rail of tabs */}
          <nav className="settings-nav">
            {SETTINGS_TABS.map((t) => {
              const Icon = t.icon
              return (
                <button
                  key={t.id}
                  className={`settings-nav-item ${t.id === tab ? 'active' : ''}`}
                  onClick={() => setTab(t.id)}
                >
                  <Icon size={16} />
                  <span>{t.label}</span>
                </button>
              )
            })}
          </nav>

          {/* the active tab's content */}
          <div className="settings-content">
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
                {active.custom === 'tools' && <ToolsAndPlugins ctx={ctx} onEdit={() => setNote('')} />}
                {active.groups.map((group) => (
                  <GroupCard key={group.title} group={group} ctx={ctx} />
                ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ---- a group of fields as one card (shared by every tab) --------------------------
function GroupCard({ group, ctx }: { group: GroupDef; ctx: Ctx }) {
  const fields = group.fields.filter((f) => {
    if (!f.showWhen) return true
    return getPath(ctx.draft, f.showWhen.key) === f.showWhen.equals
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
            envTouched={fieldScope(field.key) === 'env' && !!ctx.keysDraft[bareKey(field.key)]?.trim()}
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
    </div>
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
  listBuf,
  setListBuf,
  onChange
}: {
  field: FieldDef
  value: any
  options: ModelOption[]
  envSet: boolean
  envTouched: boolean
  listBuf: Record<string, string>
  setListBuf: (u: (b: Record<string, string>) => Record<string, string>) => void
  onChange: (v: any) => void
}) {
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
      case 'secret':
        return (
          <input
            type="password"
            className="settings-input"
            autoComplete="off"
            spellCheck={false}
            value={value ?? ''}
            placeholder={envSet ? 'Saved — leave blank to keep' : field.placeholder || 'Paste a key to add'}
            onChange={(e) => onChange(e.target.value)}
          />
        )
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
    <div className={`settings-row ${stacked ? 'settings-row--stacked' : ''}`}>
      <div className="settings-label">
        <div className="k">
          {field.label}
          {field.type === 'secret' && (
            <span className={`key-chip ${envTouched ? 'edit' : envSet ? 'ok' : ''}`}>
              {envTouched ? 'will save' : envSet ? 'set' : 'not set'}
            </span>
          )}
        </div>
        {field.help && <div className="d">{field.help}</div>}
      </div>
      <div className="settings-ctl">{control()}</div>
    </div>
  )
}

// ---- Tools & plugins tab: one catalog, grouped by plugin ---------------------------
// Each plugin is a card: a master on/off plus its tools nested, each with its own on/off and
// (where a tool takes a model) a model dropdown. Backed by plugins.catalog; edits write
// tools_disabled (per-tool), plugins[id].enabled (plugin gate) and plugins[id].tools[t].model.
interface CatTool {
  name: string
  description: string
  needsModel: boolean
  model: string | null
  provider: string | null
  enabled: boolean
}
interface CatPlugin { id: string; description: string; enabled: boolean; tools: CatTool[] }

function ToolsAndPlugins({ ctx, onEdit }: { ctx: Ctx; onEdit: () => void }) {
  const [cat, setCat] = useState<CatPlugin[] | null>(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')

  useEffect(() => {
    let alive = true
    gateway
      .request<{ plugins: CatPlugin[] }>('plugins.catalog')
      .then((r) => alive && setCat(r.plugins || []))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [])

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
  const setKnob = (pid: string, tool: string, knob: string, val: string): void => {
    onEdit()
    ctx.setDraft((d) => setPath(d, `plugins.${pid}.tools.${tool}.${knob}`, val))
  }
  const knobValue = (pid: string, tool: string, knob: string): string =>
    getPath(ctx.draft, `plugins.${pid}.tools.${tool}.${knob}`) ?? ''

  const ql = q.trim().toLowerCase()
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
    .filter((p) => p.tools.length > 0)

  return (
    <div className="settings-group">
      <div className="settings-section">Tools &amp; plugins</div>
      <p className="settings-help">
        Every capability, grouped by the plugin it belongs to. Toggle a whole plugin or a single tool,
        and set the model a tool uses. Changes take effect after the daemon restarts.
      </p>
      <div className="tools-search tp-search">
        <Search size={15} />
        <input value={q} placeholder="Search tools & plugins…" onChange={(e) => setQ(e.target.value)} />
      </div>
      {err && <div className="settings-empty" style={{ color: 'var(--danger)' }}>Couldn’t load: {err}</div>}
      {!cat && !err && <div className="settings-empty">Loading…</div>}
      {cat && plugins.length === 0 && <div className="settings-empty">No matches for “{q}”.</div>}

      {plugins.map((p) => {
        const on = pluginOn(p.id)
        return (
          <div className={`plugin-card ${on ? '' : 'plugin-off'}`} key={p.id}>
            <div className="plugin-head">
              <div className="plugin-headmain">
                <span className="plugin-name">{p.id}</span>
                {p.description && <span className="plugin-desc">{p.description}</span>}
              </div>
              <button className={`switch ${on ? 'on' : ''}`} title="enable / disable plugin" onClick={() => setPluginOn(p.id, !on)} />
            </div>
            {p.tools.map((t) => {
              const toolOn = !disabled.includes(t.name)
              const modelVal = knobValue(p.id, t.name, 'model') || t.model || ''
              const hasProvider = t.provider != null
              const providerVal = knobValue(p.id, t.name, 'provider') || t.provider || ''
              return (
                <div className="pt-row" key={t.name}>
                  <div className="pt-info">
                    <div className="pt-name">{t.name}</div>
                    {t.description && <div className="pt-desc">{t.description}</div>}
                    {(t.needsModel || hasProvider) && (
                      <div className="pt-knobs">
                        {t.needsModel && (
                          <label className="pt-knob">
                            <span>model</span>
                            <select className="settings-select" value={modelVal} onChange={(e) => setKnob(p.id, t.name, 'model', e.target.value)}>
                              {optionEls(models, modelVal)}
                            </select>
                          </label>
                        )}
                        {hasProvider && (
                          <label className="pt-knob">
                            <span>provider</span>
                            <input className="settings-input" value={providerVal} spellCheck={false} onChange={(e) => setKnob(p.id, t.name, 'provider', e.target.value)} />
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
        {rawErr && <div className="field-error" style={{ marginTop: 8 }}>{rawErr}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
          <button className="btn primary" onClick={() => void saveRaw()} disabled={rawSaving || raw === data?.raw}>
            {rawSaving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
            {rawSaving ? 'Saving…' : 'Save raw config'}
          </button>
        </div>
      </div>
    </>
  )
}
