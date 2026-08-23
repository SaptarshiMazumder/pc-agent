/* What this agent adds to the shared settings page.
 *
 * THE PAGE ITSELF IS NOT HERE. It is `common/settings/Settings`, the same page the assistant shows
 * and the same one every agent ships — including the fields this agent declared in `[[settings]]`,
 * which it renders from what the daemon sends rather than from anything written here.
 *
 * This file used to be a 331-line settings page of its own. It rendered the declared fields, the
 * provider keys, the account section and the run-mode switch, all slightly differently from the
 * assistant's — which is exactly what a user should never meet twice. `validate_agent` reports
 * that as UI_NO_SETTINGS, and it was right to.
 *
 * WHAT SURVIVED is the part the shared schema genuinely cannot know about, slotted into the tab
 * each piece belongs to:
 *
 *   Test connection -> API Keys, beside the COMFY_URL field it tests
 *   Account         -> General, because who is signed in comes before anything else
 *   Connected       -> Tools & plugins, beside the tools those services provide
 */

import { useState } from 'react'

import { useSettingsActions } from '../common/settings/SettingsActions'
import type { AuthState, RunMode } from '../agentd'

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

/** TEST, DON'T TRUST. A pasted URL that looks right and is wrong is the normal case here — an
 *  expired pod, a missing port, a stopped instance. This calls the agent's own `comfy_server`
 *  tool: no model, no tokens, so "is this correct" is answered on this page rather than three
 *  messages into a conversation. */
export function ServerTest({ onTest }: { onTest: () => Promise<string> }) {
  const page = useSettingsActions()
  const [probe, setProbe] = useState('')
  const [probing, setProbing] = useState(false)

  /** SAVE FIRST WHEN THERE ARE EDITS.
   *
   *  The test reads the SAVED value, because the tool it calls reads the agent's environment —
   *  not the form above. So pasting a URL and pressing Test used to check the PREVIOUS value and
   *  report success or failure about a server you were no longer pointing at. The button says
   *  what it will do instead of quietly doing the wrong one. */
  const test = async (): Promise<void> => {
    setProbing(true)
    setProbe('')
    try {
      if (page.dirty) await page.commit()
      setProbe(await withDeadline(onTest(), TEST_CEILING_MS))
    } catch (e) {
      // WITHOUT THIS the button sticks on "Testing…" forever and the reason is thrown away into
      // an unhandled rejection — the failure mode that looks most like a hang, on the one control
      // whose entire job is telling you whether something is wrong.
      setProbe(String(e))
    } finally {
      setProbing(false)
    }
  }

  return (
    <section className="settings-group">
      <div className="settings-section">ComfyUI server</div>
      <p className="settings-help">
        This agent does not run ComfyUI — it drives one over the network. Point it at a pod on
        RunPod or Vast, a machine on your LAN, or anything else reachable, then check it answers.
      </p>
      <div className="settings-card">
        <div className="extra-row">
          <button className="ghost-btn" disabled={probing} onClick={() => void test()}>
            {probing ? 'Testing…' : page.dirty ? 'Save & test' : 'Test connection'}
          </button>
        </div>
        {probe && <pre className="probe">{probe}</pre>}
      </div>
    </section>
  )
}

/** WHO is signed in, and WHOSE KEYS PAY. Two questions, deliberately separate.
 *
 *  They used to be one, and the mistake it caused is worth remembering: signing in was treated as
 *  choosing to be billed, so on a BYOK install — where nobody is paying anyone — the product
 *  concluded there was nothing to ask and never offered a login at all.
 *
 *  Both facts belong to THIS window. Two windows on one machine can be two people on two billing
 *  modes; the daemon stores neither and reads both off each connection. Which is why changing
 *  either reconnects — done by the SDK, not here.
 */
export function AccountSection({
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
      <section className="settings-group">
        <div className="settings-section">Account</div>
        <p className="settings-help">
          This daemon has no accounts service configured, so there is no sign-in. Model calls run
          on the API key set under API Keys.
        </p>
      </section>
    )
  }

  return (
    <section className="settings-group">
      <div className="settings-section">Account</div>
      <div className="settings-card">
        {auth.signedIn ? (
          <div className="extra-row">
            <span>
              Signed in as <strong>{auth.email}</strong>
            </span>
            <button className="ghost-btn" onClick={onSignOut} disabled={busy}>
              Sign out
            </button>
          </div>
        ) : (
          <p className="settings-help">Not signed in on this window.</p>
        )}

        {auth.canUseCloud && (
          <>
            <div className="extra-label">Which keys pay for model calls</div>
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
      </div>
    </section>
  )
}

/** Only for an agent that declares MCP servers. Empty here, and rendering nothing is correct — a
 *  heading over an empty list reads as something being broken. */
export function ServicesSection({ mcp }: { mcp: any[] }) {
  if (!mcp.length) return null
  return (
    <section className="settings-group">
      <div className="settings-section">Connected services</div>
      <div className="settings-card">
        {mcp.map((s) => (
          <div key={s.name} className="extra-row">
            <strong>{s.name}</strong>
            <span className="muted">{s.transport}</span>
            {s.problem ? (
              <span className="panel-error">{s.problem}</span>
            ) : (
              <span className="muted">{s.tools?.length ?? 0} tool(s)</span>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
