import { useEffect, useState } from 'react'
import type { SettingField } from '../agentd'

/** The settings page — an agent that declares [[settings]] must give the user somewhere to set
 *  them.
 *
 *  WHY THIS EXISTS AT ALL. An agent shipping its own `entry` REPLACES the built-in window,
 *  including whatever settings UI the shell would have shown. Declaring a setting and building no
 *  way to fill it leaves the user with a feature that silently never runs — the heartbeat reads an
 *  empty watch list forever and reports "nothing to do", which looks exactly like working.
 *
 *  A SECRET IS NEVER READ BACK. The daemon returns non-secret values and, for secrets, only
 *  whether one is stored. So a secret field shows "stored" and an empty box to replace it. */
export function Settings({
  fields,
  values,
  present,
  error,
  onSave,
}: {
  fields: SettingField[]
  values: Record<string, string>
  present: Record<string, boolean>
  error: string
  onSave: (patch: Record<string, string>) => Promise<string>
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setDraft(values)
  }, [values])

  const dirty = fields.some((f) => (draft[f.key] ?? '') !== (values[f.key] ?? ''))

  const submit = async () => {
    setSaving(true)
    setSaved(false)
    const patch: Record<string, string> = {}
    for (const f of fields) {
      const next = draft[f.key] ?? ''
      if (next !== (values[f.key] ?? '')) patch[f.key] = next
    }
    // The refusal comes back as a VALUE, not an exception — reporting "Saved" here without
    // checking is how a user ends up trusting a key that was never written.
    const why = await onSave(patch)
    setFailed(why)
    setSaved(!why)
    setSaving(false)
  }

  if (error) return <p className="err">could not read settings: {error}</p>

  return (
    <div className="scroll">
      <div className="page-head">
        <h1>Settings</h1>
        <p className="muted">
          Declared by this agent in <code>agent.toml</code>. Values are stored on this machine and
          never travel with the agent when it is packaged.
        </p>
      </div>

      {fields.length === 0 && (
        <p className="muted pad">This agent declares no settings.</p>
      )}

      <div className="fields">
        {fields.map((f) => {
          const secret = f.kind === 'secret' || f.kind === 'password'
          return (
            <label key={f.key} className="field">
              <span className="field-label">
                {f.label || f.key}
                {f.required && <span className="req">required</span>}
                {secret && present[f.key] && <span className="stored">stored</span>}
              </span>
              {f.help && <span className="field-help">{f.help}</span>}
              <input
                type={secret ? 'password' : 'text'}
                value={draft[f.key] ?? ''}
                placeholder={secret && present[f.key] ? '•••••••• (leave blank to keep)' : ''}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
              />
              <span className="field-key">{f.key}</span>
            </label>
          )
        })}
      </div>

      {fields.length > 0 && (
        <div className="actions">
          <button className="primary" disabled={!dirty || saving} onClick={() => void submit()}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {saved && <span className="ok-note">Saved.</span>}
          {failed && <span className="err">{failed}</span>}
        </div>
      )}
    </div>
  )
}
