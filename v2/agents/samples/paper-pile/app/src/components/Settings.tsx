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
/** NOT EVERY FIELD IS WORTH A TEST BUTTON. A folder either exists and holds documents or it does
 *  not, and that is worth checking. A list of topics to watch has nothing to check — a "Test"
 *  beside it could only ever say "looks fine", which teaches the user that the button means
 *  nothing, including next to the field where it does. So exactly one field has one.
 *
 *  And it SAVES BEFORE TESTING: proving the old value works tells you nothing about the new one. */
export function Settings({
  fields,
  values,
  present,
  error,
  onSave,
  onTest,
}: {
  fields: SettingField[]
  values: Record<string, string>
  present: Record<string, boolean>
  error: string
  onSave: (patch: Record<string, string>) => Promise<string>
  onTest: (key: string, value: string) => Promise<string>
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState('')
  const [tested, setTested] = useState<Record<string, string>>({})
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
              <span className="field-row">
                <span className="field-key">{f.key}</span>
                {f.key === 'PAPER_PILE_INBOX' && (
                  <button
                    className="ghost small"
                    disabled={!draft[f.key]?.trim() || testing === f.key}
                    onClick={async (e) => {
                      e.preventDefault()
                      setTesting(f.key)
                      // SAVE FIRST. Testing the box while the agent still reads the old value
                      // proves nothing about what will actually happen on the next run.
                      const why = await onSave({ [f.key]: draft[f.key] ?? '' })
                      const result = why ? `could not save: ${why}` : await onTest(f.key, draft[f.key] ?? '')
                      setTested((prev) => ({ ...prev, [f.key]: result }))
                      setTesting('')
                    }}
                  >
                    {testing === f.key ? 'checking…' : 'Test'}
                  </button>
                )}
              </span>
              {tested[f.key] && <span className="field-help">{tested[f.key]}</span>}
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
