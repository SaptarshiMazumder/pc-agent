/* The run in flight — one line, and only while there is one.
 *
 * WHAT IT REPLACES. A panel with a heading, a chip, a progress track, a node-ish row list and an
 * Interrupt button, permanently occupying a column whether or not anything was running. Its
 * honest content is three facts (which workflow, how long, can I stop it), and three facts are a
 * strip, not a panel. When nothing is running this renders NOTHING rather than an empty card
 * saying "idle" — a box that exists only to say it has nothing to say is the clutter being cut.
 *
 * THE BAR IS STILL AN ESTIMATE, against the recent average, and still says so. ComfyUI reports
 * per-node progress only over its websocket, which the bridge does not hold; a node ticker
 * animating on a timer would be theatre.
 */

import { useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import type { StudioState } from './useStudioState'

export function ActiveRunStrip({
  state,
  client,
}: {
  state: StudioState
  client: AgentdClient | undefined
}) {
  const [stopping, setStopping] = useState(false)
  const active = state.active
  if (!active) return null

  const runs = state.runs || []
  const done = runs.filter((r) => r.status === 'complete' && r.duration > 0)
  const avg = done.length ? done.reduce((s, r) => s + r.duration, 0) / done.length : null
  const pct = avg ? Math.min(97, Math.round((active.elapsed / avg) * 100)) : null

  const stop = async (): Promise<void> => {
    if (!client || stopping) return
    setStopping(true)
    try {
      await client.request('tools.invoke', { name: 'comfy_interrupt', params: {} })
    } catch {
      /* the refusal lands in the conversation's next run report either way */
    } finally {
      setStopping(false)
    }
  }

  return (
    <div className="rs">
      <span className="rs-dot" />
      <span className="rs-file st-mono">{active.workflow || 'workflow'}</span>
      {active.checkpoint && <span className="rs-meta st-mono">{active.checkpoint}</span>}
      <span className="rs-meta">{Math.round(active.elapsed)}s</span>
      <span className="rs-track">
        <span
          className={pct == null ? 'is-indeterminate' : ''}
          style={pct == null ? undefined : { width: `${pct}%` }}
        />
      </span>
      {pct != null && <span className="rs-meta">~{pct}%</span>}
      <button className="rs-stop" onClick={() => void stop()} disabled={stopping || !client}>
        {stopping ? 'stopping…' : 'Interrupt'}
      </button>
    </div>
  )
}
