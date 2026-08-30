/* The run-mode badge for the agentd shell — always on screen, click to switch.
 *
 * It reads the shell's `useMode()`, which now ADOPTS the daemon's persisted mode on every connect
 * and every `runmode.changed` broadcast (lib/mode.ts) — so it can never say Cloud while a call runs
 * Local. `setMode` persists the flip on the daemon (config.set). Locked (no toggle) on hosted.
 *
 * A COUSIN of the agents' `_common/runmode/RunModeBadge`: same look, but wired to the shell's own
 * mode store rather than the SDK, because the shell speaks to the daemon through its own gateway.
 */

import { setMode, useMode } from '../lib/mode'
import { useApp } from '../state/store'
import './runmode.css'

export default function RunModeBadge() {
  const mode = useMode()
  const locked = useApp((s) => !!s.hello?.platform?.runModeLocked)

  if (!mode) return null // no choice adopted yet — the launcher is showing instead

  const cloud = mode === 'cloud'
  const toggle = () => {
    if (locked) return
    setMode(cloud ? 'local' : 'cloud')
  }

  const title = locked
    ? 'Cloud — platform keys (metered). The only mode on the web.'
    : cloud
      ? 'Cloud — platform keys, metered to your account. Click for Local (your own keys).'
      : 'Local — your own API keys, no metering. Click for Cloud (platform keys).'

  return (
    <button
      className={`runmode ${cloud ? 'runmode--cloud' : 'runmode--local'} ${locked ? 'runmode--locked' : ''}`}
      onClick={toggle}
      disabled={locked}
      title={title}
      aria-label={`Run mode: ${cloud ? 'Cloud' : 'Local'}${locked ? ' (locked)' : ''}`}
    >
      <span className="runmode-dot" />
      <span className="runmode-label">{cloud ? 'Cloud' : 'Local'}</span>
    </button>
  )
}
