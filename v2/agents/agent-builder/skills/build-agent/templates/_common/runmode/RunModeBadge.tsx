/* The run-mode badge — always on screen, tells you whose keys pay for model calls, click to switch.
 *
 * ONE source of truth: it reads the mode from the DAEMON (`authStatus().mode`), never a client-side
 * guess, so it can never say Cloud while a call runs Local. Clicking toggles via `setRunMode`, which
 * persists the choice on the daemon (config.set) — so the switch is the same in every window, and
 * the daemon's `runmode.changed` broadcast updates every OTHER open window live.
 *
 * LOCKED on hosted: there is no BYOK there (keys are refused, no per-account key store), so cloud is
 * the only runnable option. The badge then shows a static "Cloud" with no toggle.
 *
 * A COPY of this lives in the agentd shell too (clients/ui) — the display is cheap and each surface
 * renders its own; the LOGIC it calls (authStatus/setRunMode) is the one shared SDK.
 */

import { authStatus, setRunMode, type AgentdClient, type AuthState } from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'

import './runmode.css'

export default function RunModeBadge({ client }: { client?: AgentdClient }) {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    void authStatus({ client })
      .then(setAuth)
      .catch(() => {
        /* connection trouble — the app reports that itself; the badge holds its last value */
      })
  }, [client])

  useEffect(() => {
    refresh()
    // Update live when the mode changes ANYWHERE — the daemon broadcasts `runmode.changed` on a
    // config.set that touches run_mode, so another window's flip reaches this one. Also re-read on
    // every (re)connect: signing in or reconnecting can change what the daemon resolves.
    const offMode = client?.on('runmode.changed', refresh)
    const offOpen = client?.onStatus((s) => s === 'open' && refresh())
    return () => {
      offMode?.()
      offOpen?.()
    }
  }, [refresh, client])

  if (!auth) return null // nothing to say until the first read settles

  const cloud = auth.mode === 'cloud'
  const locked = auth.modeLocked

  const toggle = async () => {
    if (locked || busy) return
    setBusy(true)
    setError('')
    try {
      await setRunMode(cloud ? 'local' : 'cloud', { client })
      refresh()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const label = cloud ? 'Cloud' : 'Local'
  const title = locked
    ? 'Cloud — platform keys (metered). The only mode on the web.'
    : cloud
      ? 'Cloud — platform keys, metered to your account. Click for Local (your own keys).'
      : 'Local — your own API keys, no metering. Click for Cloud (platform keys).'

  return (
    <button
      className={`runmode ${cloud ? 'runmode--cloud' : 'runmode--local'} ${locked ? 'runmode--locked' : ''}`}
      onClick={toggle}
      disabled={locked || busy}
      title={error || title}
      aria-label={`Run mode: ${label}${locked ? ' (locked)' : ''}`}
    >
      <span className="runmode-dot" />
      <span className="runmode-label">{busy ? '…' : label}</span>
    </button>
  )
}
