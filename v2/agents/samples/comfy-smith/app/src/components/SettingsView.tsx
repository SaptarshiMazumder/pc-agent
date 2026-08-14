/* Settings, inside the agent's own window.
 *
 * WHY THIS EXISTS AT ALL. An agent that is missing a credential does not look like an agent that
 * is missing a credential — it looks broken. It says it cannot reach the server, the user tries
 * again, it says the same thing, and nothing on screen connects that to an empty field. The
 * settings page is where a failure becomes a fix, so it belongs in the window the failure
 * happened in.
 *
 * WHAT IT RENDERS COMES FROM agent.toml. `config.get` returns this agent's own [[settings]] —
 * key, label, kind, required, help — so the form is generated from the declaration rather than
 * hand-written here. Add a field to agent.toml and it appears; nothing in this file changes.
 *
 * TEST, DON'T TRUST. A pasted URL that looks right and is wrong is the normal case (an expired
 * pod, a missing port, a stopped instance). The Test button calls the agent's own `comfy_server`
 * tool — no model, no tokens — so "is this correct" is answered here rather than three messages
 * into a conversation.
 */

import { useState } from 'react'
import type { AuthState, RunMode, SettingField, SettingsSurface } from '../agentd'

/** The probe's own HTTP timeout is 60s, so anything past this is not a slow server — it is a
 *  request that will never come back. A socket request has no timeout of its own: the promise
 *  stays pending until the connection drops, which on a healthy socket is never. */
const TEST_CEILING_MS = 75_000

function withDeadline<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout>
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new Error(
            `No answer from the agent after ${Math.round(ms / 1000)}s. The daemon may be busy ` +
              `or the tool may be stuck — check the Server page, or reopen this window.`,
          ),
        ),
      ms,
    )
  })
  return Promise.race([work, deadline]).finally(() => clearTimeout(timer)) as Promise<T>
}

export function SettingsView({
  data,
  error,
  onSave,
  onTest,
  mcp,
  auth,
  authBusy,
  authError,
  onSignOut,
  onMode,
}: {
  data: SettingsSurface | null
  error: string
  onSave: (keys: Record<string, string>) => Promise<string>
  onTest: () => Promise<string>
  mcp: any[]
  auth: AuthState | null
  authBusy: boolean
  authError: string
  onSignOut: () => void
  onMode: (mode: RunMode) => void
}) {
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saved, setSaved] = useState(false)
  const [probe, setProbe] = useState('')
  const [probing, setProbing] = useState(false)

  if (error) return <div className="view settings"><p className="panel-error">{error}</p></div>
  if (!data) return <div className="view settings"><p className="panel-empty">Loading…</p></div>

  const valueOf = (f: SettingField) =>
    f.key in edits ? edits[f.key] : (data.settingsValues?.[f.key] ?? '')

  const dirty = Object.keys(edits).length > 0

  const save = async (): Promise<boolean> => {
    setSaving(true)
    setSaved(false)
    const problem = await onSave(edits)
    setSaving(false)
    setSaveError(problem)
    if (problem) return false
    setEdits({})
    setSaved(true)
    return true
  }

  /** SAVE FIRST WHEN THERE ARE EDITS.
   *
   *  The test reads the SAVED value, because the tool it calls reads the agent's environment —
   *  not this form. So pasting a URL and pressing Test used to check the PREVIOUS value and
   *  report success or failure about a server you were no longer pointing at. The button says
   *  what it will do instead of quietly doing the wrong one. */
  const test = async () => {
    setProbing(true)
    setProbe('')
    try {
      if (dirty && !(await save())) return
      setProbe(await withDeadline(onTest(), TEST_CEILING_MS))
    } catch (e) {
      // WITHOUT THIS the button sticks on "Testing…" forever and the reason is thrown away into
      // an unhandled rejection — the failure mode that looks most like a hang, on the one
      // control whose entire job is telling you whether something is wrong.
      setProbe(String(e))
    } finally {
      setProbing(false)
    }
  }

  return (
    <div className="view settings">
      <header className="view-head">
        <h1>Settings</h1>
        <span className="muted">
          v{data.version} · {data.effectiveModel}
        </span>
      </header>

      <Account
        auth={auth}
        busy={authBusy}
        error={authError}
        onSignOut={onSignOut}
        onMode={onMode}
      />

      <section className="group">
        <h2>ComfyUI server</h2>
        <p className="muted">
          This agent does not run ComfyUI — it drives one over the network. Point it at a pod on
          RunPod or Vast, a machine on your LAN, or anything else reachable.
        </p>

        {data.settings.map((f) => (
          <Field
            key={f.key}
            field={f}
            value={valueOf(f)}
            present={!!data.env?.[f.key]}
            onChange={(v) => setEdits((prev) => ({ ...prev, [f.key]: v }))}
          />
        ))}

        <div className="actions">
          <button className="primary" onClick={() => void save()} disabled={!dirty || saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button className="ghost" disabled={probing || saving} onClick={() => void test()}>
            {probing ? 'Testing…' : dirty ? 'Save & test' : 'Test connection'}
          </button>
          {saved && <span className="ok-note">Saved</span>}
        </div>

        {saveError && <p className="panel-error">{saveError}</p>}
        {probe && <pre className="probe">{probe}</pre>}
      </section>

      {/* Provider keys are the machine's, not this agent's — shown so "why did nothing happen"
          has an answer here too, but write-only and read-only in cloud mode. */}
      <section className="group">
        <h2>API keys</h2>
        {data.keysLocked ? (
          <p className="muted">
            This window runs on Cloud, so model calls are paid for by the platform's keys and
            metered to your account. Switch to Local above to use your own.
          </p>
        ) : (
          <>
            <p className="muted">
              The key the agent thinks with. Stored on this machine; never readable back.
            </p>
            {(data.providerKeys ?? []).map((key) => (
              <Field
                key={key}
                field={{ key, label: key, kind: 'secret', required: false, help: '' }}
                value={key in edits ? edits[key] : ''}
                present={!!data.env?.[key]}
                onChange={(v) => setEdits((prev) => ({ ...prev, [key]: v }))}
              />
            ))}
          </>
        )}
      </section>

      {/* Only for an agent that declares MCP servers. Empty here, and rendering nothing is
          correct — a heading over an empty list reads as something being broken. */}
      {mcp.length > 0 && (
        <section className="group">
          <h2>Connected services</h2>
          {mcp.map((s) => (
            <div key={s.name} className="mcp-row">
              <strong>{s.name}</strong>
              <span className="muted">{s.transport}</span>
              {s.problem ? (
                <span className="panel-error">{s.problem}</span>
              ) : (
                <span className="muted">{s.tools?.length ?? 0} tool(s)</span>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  )
}

/** WHO is signed in, and WHOSE KEYS PAY. Two questions, deliberately separate.
 *
 *  They used to be one, and the mistake it caused is worth remembering: signing in was treated
 *  as choosing to be billed, so on a BYOK install — where nobody is paying anyone — the product
 *  concluded there was nothing to ask and never offered a login at all.
 *
 *  Both facts belong to THIS window. Two windows on one machine can be two people on two billing
 *  modes; the daemon stores neither and reads both off each connection. Which is why changing
 *  either reconnects — done by the SDK, not here.
 */
function Account({
  auth,
  busy,
  error,
  onSignOut,
  onMode,
}: {
  auth: AuthState | null
  busy: boolean
  error: string
  onSignOut: () => void
  onMode: (mode: RunMode) => void
}) {
  if (!auth) return null

  if (!auth.available) {
    // Not a failure, and not something to hide: a BYOK install with no accounts service has
    // nobody to sign in to. Saying it beats a section that is mysteriously empty.
    return (
      <section className="group">
        <h2>Account</h2>
        <p className="muted">
          This daemon has no accounts service configured, so there is no sign-in. Model calls run
          on the API key set below.
        </p>
      </section>
    )
  }

  return (
    <section className="group">
      <h2>Account</h2>
      {auth.signedIn ? (
        <div className="account-row">
          <span>
            Signed in as <strong>{auth.email}</strong>
          </span>
          <button className="ghost" onClick={onSignOut} disabled={busy}>
            Sign out
          </button>
        </div>
      ) : (
        <p className="muted">Not signed in on this window.</p>
      )}

      {auth.canUseCloud && (
        <>
          <span className="field-label" style={{ marginTop: 14 }}>
            Which keys pay for model calls
          </span>
          <div className="seg">
            <button
              className={auth.mode === 'local' ? 'on' : ''}
              disabled={busy}
              onClick={() => onMode('local')}
            >
              Local
              <span>your own API key</span>
            </button>
            <button
              className={auth.mode === 'cloud' ? 'on' : ''}
              disabled={busy || !auth.signedIn}
              onClick={() => onMode('cloud')}
              title={auth.signedIn ? '' : 'Sign in first — Cloud meters usage to your account'}
            >
              Cloud
              <span>platform keys, metered to you</span>
            </button>
          </div>
        </>
      )}

      {error && <p className="panel-error">{error}</p>}
    </section>
  )
}

function Field({
  field,
  value,
  present,
  onChange,
}: {
  field: SettingField
  value: string
  present: boolean
  onChange: (v: string) => void
}) {
  const isSecret = field.kind === 'secret'
  return (
    <label className="field">
      <span className="field-label">
        {field.label || field.key}
        {field.required && <span className="req"> required</span>}
        {/* A secret never comes back from the server, so the ONLY way to show it is set is this
            marker. Without it an empty password box means both "not configured" and "configured,
            and I cannot show you" — and the user retypes a key that was already right. */}
        {isSecret && present && <span className="set-note"> saved</span>}
      </span>
      <input
        type={isSecret ? 'password' : 'text'}
        value={value}
        placeholder={isSecret && present ? '•••••••• (leave empty to keep)' : ''}
        onChange={(e) => onChange(e.target.value)}
      />
      {field.help && <span className="field-help">{field.help}</span>}
    </label>
  )
}
