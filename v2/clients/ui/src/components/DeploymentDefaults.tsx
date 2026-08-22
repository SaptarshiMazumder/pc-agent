/**
 * DEPLOYMENT DEFAULTS — the admin half of per-account config.
 *
 * Everything else in the Admin page talks to the accounts service (users, credits, creators).
 * This panel talks to the DAEMON, because the config every tenant inherits is the daemon's own
 * file on shared storage, and only the daemon can write it. `config.set {target:'master'}` is
 * admitted for an identity in AGENTD_ADMIN_IDENTITIES and refused for everyone else.
 *
 * WHAT A SAVE HERE MEANS, and why it is safe to do with people connected: these are DEFAULTS, and
 * they sit at the bottom of three layers —
 *
 *     deployment defaults  (here)
 *       ⊕ each user's own config      (their Settings)
 *         ⊕ each user's per-agent overrides   (an agent's Settings tab)
 *
 * so a user who has chosen a model keeps it, and a user who has not moves with the change on
 * their next message. Nobody is signed out and nothing restarts.
 *
 * The panel reads the MASTER values, never the admin's own — an admin is a user too, and showing
 * their personal override here would have them edit the wrong number and find out from somebody
 * else's complaint.
 */

import { useCallback, useEffect, useState } from 'react'
import { Cpu, RefreshCw, ShieldCheck } from 'lucide-react'

import { gateway } from '../gateway/client'

interface ModelOption {
  value: string
  label?: string
  group?: string
}

interface MasterConfig {
  values: Record<string, any>
  catalogs?: Record<string, ModelOption[]>
  isAdmin?: boolean
  target?: string
  path?: string
}

/** One labelled control row, matching the Settings page's shape. */
function Row({
  label,
  help,
  children
}: {
  label: string
  help?: string
  children: React.ReactNode
}) {
  return (
    <div className="settings-row">
      <div className="settings-label">
        <div className="k">{label}</div>
        {help && <div className="d">{help}</div>}
      </div>
      <div className="settings-ctl">{children}</div>
    </div>
  )
}

function ModelSelect({
  value,
  options,
  onChange,
  allowInherit
}: {
  value: string
  options: ModelOption[]
  onChange: (v: string) => void
  allowInherit?: string
}) {
  const known = options.some((o) => o.value === value)
  return (
    <select className="settings-input" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowInherit && <option value="">{allowInherit}</option>}
      {/* A model the config names but the catalog does not must still be selectable, or opening
          this page would silently propose changing it. */}
      {!known && value && <option value={value}>{value} (not in catalog)</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label || o.value}
        </option>
      ))}
    </select>
  )
}

export default function DeploymentDefaults() {
  const [cfg, setCfg] = useState<MasterConfig | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setError('')
    try {
      const res = (await gateway.request('config.get', { target: 'master' })) as MasterConfig
      setCfg(res)
      if (res.target !== 'master') {
        setError('This daemon did not answer with its deployment config — you may not be an admin of it.')
      }
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const save = async (patch: Record<string, unknown>): Promise<void> => {
    setBusy(true)
    setNote('')
    setError('')
    try {
      const res = (await gateway.request('config.set', { target: 'master', patch })) as {
        saved?: boolean
        error?: string
      }
      if (res?.saved === false) {
        setError(res.error || 'not saved')
      } else {
        setNote('Saved — applies to every account that has not overridden it.')
        await load()
      }
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  if (!cfg) {
    return (
      <div className="settings-group">
        {error ? <div className="banner banner-error">{error}</div> : <p className="settings-help">Loading…</p>}
      </div>
    )
  }

  const values = cfg.values || {}
  const catalogs = cfg.catalogs || {}
  const textModels = catalogs.text || catalogs.models || []
  const ce = (values.cost_efficiency && typeof values.cost_efficiency === 'object'
    ? values.cost_efficiency
    : {}) as Record<string, any>
  const defaults = (values.model_defaults && typeof values.model_defaults === 'object'
    ? values.model_defaults
    : {}) as Record<string, any>

  const saveCe = (patch: Record<string, unknown>) =>
    save({ cost_efficiency: { ...ce, ...patch } })
  const saveKind = (kind: string, model: string) =>
    save({ model_defaults: { ...defaults, [kind]: model } })

  return (
    <>
      {error && <div className="banner banner-error">{error}</div>}

      <div className="settings-group">
        <div className="settings-section">
          <ShieldCheck size={13} />
          What every account starts from
        </div>
        <p className="settings-help">
          These are the deployment's defaults. A user who has chosen their own model keeps it; a
          user who has not follows whatever is set here, from their next message. No restart, and
          nobody is signed out. {cfg.path ? <code>{cfg.path}</code> : null}
        </p>
      </div>

      <div className="settings-group">
        <div className="settings-section">
          <Cpu size={13} />
          The brain
        </div>
        <div className="settings-card">
          <Row label="Model" help="answers in chat and decides which tools to run">
            <ModelSelect
              value={String(values.model || '')}
              options={textModels}
              onChange={(v) => void save({ model: v })}
            />
          </Row>
          <Row label="Reasoning effort" help="how much the model thinks before answering">
            <select
              className="settings-input"
              value={String(values.reasoning_effort || 'off')}
              onChange={(e) => void save({ reasoning_effort: e.target.value })}
            >
              {['off', 'low', 'medium', 'high'].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </Row>
          <Row
            label="Fallbacks"
            help="tried in order when the model above cannot serve a turn — comma separated"
          >
            <input
              className="settings-input"
              defaultValue={(Array.isArray(values.model_fallbacks) ? values.model_fallbacks : []).join(', ')}
              onBlur={(e) =>
                void save({
                  model_fallbacks: e.target.value
                    .split(',')
                    .map((s) => s.trim())
                    .filter(Boolean)
                })
              }
            />
          </Row>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-section">
          <Cpu size={13} />
          Cost routing
        </div>
        <p className="settings-help">
          When on, ordinary text turns run on the cheap model and only turns carrying an image
          escalate to the vision model. A dead or unfunded model here is felt by every account that
          has not overridden it.
        </p>
        <div className="settings-card">
          <Row label="Enabled">
            <button
              className={`switch ${ce.enabled ? 'on' : ''}`}
              onClick={() => void saveCe({ enabled: !ce.enabled })}
            />
          </Row>
          {!!ce.enabled && (
            <>
              <Row label="Text model" help="ordinary turns">
                <ModelSelect
                  value={String(ce.text_model || '')}
                  options={textModels}
                  allowInherit="— use the brain model —"
                  onChange={(v) => void saveCe({ text_model: v })}
                />
              </Row>
              <Row label="Vision model" help="turns that carry an image the model must see">
                <ModelSelect
                  value={String(ce.vision_model || '')}
                  options={catalogs.vision || textModels}
                  allowInherit="— use the brain model —"
                  onChange={(v) => void saveCe({ vision_model: v })}
                />
              </Row>
            </>
          )}
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-section">
          <Cpu size={13} />
          House models by kind
        </div>
        <p className="settings-help">
          What a tool gets when it declares a kind and names no model of its own — so one change
          here moves every tool of that kind, for every account that has not overridden it.
        </p>
        <div className="settings-card">
          <Row label="Vision" help="tools that read and judge images">
            <ModelSelect
              value={String(defaults.vision || '')}
              options={catalogs.vision || textModels}
              allowInherit="— built-in default —"
              onChange={(v) => void saveKind('vision', v)}
            />
          </Row>
          <Row label="Image generation" help="tools that draw">
            <ModelSelect
              value={String(defaults['image-gen'] || '')}
              options={catalogs['image-gen'] || []}
              allowInherit="— built-in default —"
              onChange={(v) => void saveKind('image-gen', v)}
            />
          </Row>
          <Row label="Embedding" help="memory and search indexing">
            <ModelSelect
              value={String(defaults.embedding || '')}
              options={catalogs.embedding || []}
              allowInherit="— built-in default —"
              onChange={(v) => void saveKind('embedding', v)}
            />
          </Row>
        </div>
      </div>

      <div className="settings-group">
        <button className="btn" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={14} /> Reload
        </button>
        {note && <span className="settings-help"> {note}</span>}
      </div>
    </>
  )
}
