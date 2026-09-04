/* The run in flight, from the bridge's own telemetry. What is real: workflow file, checkpoint,
 * steps, elapsed, state — and the Interrupt button, which really calls comfy_interrupt.
 *
 * NO PER-NODE TICKER. ComfyUI reports node-by-node progress only over its websocket, which the
 * bridge does not hold; a node list animating on a timer would be theatre. The progress bar is
 * an ESTIMATE against the recent average run time, and says so.
 */

import { useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import type { StudioState } from './useStudioState'

export function ActiveRunPanel({
  state,
  client,
}: {
  state: StudioState
  client: AgentdClient | undefined
}) {
  const [stopping, setStopping] = useState(false)
  const active = state.active
  const runs = state.runs || []
  const lastDone = runs.find((r) => r.status === 'complete')
  const avg = (() => {
    const done = runs.filter((r) => r.status === 'complete').slice(0, 5)
    return done.length ? done.reduce((s, r) => s + r.duration, 0) / done.length : null
  })()
  const estimate =
    active && avg ? Math.min(95, Math.round((active.elapsed / avg) * 100)) : null

  const interrupt = async () => {
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
    <section className="st-panel st-active">
      <div className="st-panel-head">
        <h2>Active run</h2>
        <span className={`st-run-chip ${active ? 'is-running' : ''}`}>
          {active ? 'running' : 'idle'}
        </span>
      </div>

      {active ? (
        <>
          <div className="st-active-file">
            <span className="st-mono">{active.workflow}</span>
            <span className="st-active-pct">
              {estimate != null ? `~${estimate}%` : `${Math.round(active.elapsed)}s`}
            </span>
          </div>
          <div className="st-track">
            <span
              className={estimate == null ? 'is-indeterminate' : ''}
              style={estimate != null ? { width: `${estimate}%` } : undefined}
            />
          </div>
          <div className="st-active-rows">
            {active.checkpoint && (
              <div className="st-row">
                <span className="st-row-dot is-done" />
                <span className="st-row-name">{active.checkpoint}</span>
                <span className="st-row-metric">loaded</span>
              </div>
            )}
            <div className="st-row">
              <span className="st-row-dot is-running" />
              <span className="st-row-name is-live">sampling</span>
              <span className="st-row-metric is-live">
                {active.steps ? `${active.steps} steps` : `${Math.round(active.elapsed)}s`}
              </span>
            </div>
          </div>
        </>
      ) : (
        <p className="st-empty">
          {lastDone
            ? `Last run: ${lastDone.name} — ${lastDone.duration.toFixed(1)}s, ${lastDone.outputs} output(s).`
            : 'No run yet this session. Ask for a workflow in the conversation.'}
        </p>
      )}

      <div className="st-active-actions">
        <button className="st-btn" disabled={!active || stopping} onClick={() => void interrupt()}>
          {stopping ? 'Interrupting…' : 'Interrupt'}
        </button>
        <button className="st-btn is-ghost" disabled title="Graph view is not built yet">
          Open graph
        </button>
      </div>
    </section>
  )
}
