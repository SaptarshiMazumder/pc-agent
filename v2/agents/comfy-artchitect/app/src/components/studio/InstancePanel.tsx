/* What the user's ComfyUI actually holds — and, first, WHETHER IT IS THERE.
 *
 * The model list is telemetry: the last probe/inventory the agent ran, cached in the workspace. It
 * is worth showing, but it cannot answer "is my instance up", and rendering it unconditionally
 * answered that question wrongly — a stopped box still listed its models, so the panel looked
 * healthy while nothing worked. Connection state now comes from a LIVE probe (useInstanceProbe)
 * and the cache is only shown behind it: connected => models, with when they were read; down =>
 * the instance's own error and what to do about it, and no stale list pretending otherwise.
 *
 * A model row lights up when it is the one the current/last run loaded. No sizes: the HTTP API
 * does not report them, and a made-up "6.9 GB" would be worse than none.
 */

import { Plug, RefreshCw } from 'lucide-react'

import type { AgentdClient } from '@agentd/client'
import type { StudioState } from './useStudioState'
import { useInstanceProbe } from './useInstanceProbe'

/** "2 min ago" — a cache is only meaningful with its age attached. */
function ago(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)}h ago`
}

export function InstancePanel({
  state,
  query,
  client,
}: {
  state: StudioState
  query: string
  client?: AgentdClient
}) {
  const probe = useInstanceProbe(client)
  const inst = state.instance
  const q = query.trim().toLowerCase()
  const models = (inst?.models || []).filter(
    (m) => !q || m.name.toLowerCase().includes(q) || m.loader.toLowerCase().includes(q),
  )
  const activeCkpt = state.active?.checkpoint || (state.runs || [])[0]?.checkpoint || ''

  // The probe's own first line is the freshest description of the box; the cached fields are the
  // fallback for the moment before the first test returns.
  const headline =
    probe.state === 'live'
      ? probe.detail.split('\n')[0]
      : inst?.version
        ? `ComfyUI ${inst.version}`
        : ''

  return (
    <section className="st-panel">
      <div className="st-panel-head">
        <h2>On this instance</h2>
        <button
          className={`st-conn-btn is-${probe.state}`}
          onClick={probe.test}
          disabled={probe.state === 'probing' || !client}
          title="Check the ComfyUI connection now and refresh what is installed"
        >
          <RefreshCw size={12} strokeWidth={2} className={probe.state === 'probing' ? 'spin' : ''} />
          <span>
            {probe.state === 'probing'
              ? 'testing…'
              : probe.state === 'live'
                ? 'connected'
                : probe.state === 'down'
                  ? 'not connected'
                  : 'test connection'}
          </span>
        </button>
      </div>

      {probe.state === 'down' ? (
        /* THE FAILURE, AND THE FIX. The tool's own words name which of the three it is — no URL
           set, a refused credential, or a box that is not running — so they are shown verbatim
           rather than replaced by a guess. No model list here: it would be from another machine. */
        <div className="st-conn-down">
          <p className="st-conn-error st-mono">{probe.error || 'the instance did not answer'}</p>
          <p className="st-panel-note">
            Set <span className="st-mono">COMFYUI_URL</span> in Settings to the full URL your
            provider gave you (vast/RunPod include a <span className="st-mono">?token=</span> —
            paste it whole), or paste that URL straight into the conversation and the agent will
            use it. If the URL is right, the box itself is probably not running.
          </p>
        </div>
      ) : (
        <>
          {headline && <p className="st-instance-gpu st-mono">{headline}</p>}
          {probe.state === 'live' && probe.detail.split('\n')[1] && (
            <p className="st-instance-gpu st-mono">{probe.detail.split('\n')[1]}</p>
          )}
          {models.length === 0 ? (
            <p className="st-empty">
              {probe.state === 'live'
                ? 'Connected, but no models are installed yet — ask the agent to build something and it will install what the workflow needs.'
                : inst
                  ? 'Nothing read yet — press “test connection”.'
                  : 'Not probed yet — press “test connection”, or just ask for a workflow.'}
            </p>
          ) : (
            <div className="st-instance-rows">
              {models.slice(0, 12).map((m) => (
                <div key={`${m.loader}/${m.name}`} className="st-row">
                  <span className={`st-row-square${m.name === activeCkpt ? ' is-loaded' : ''}`} />
                  <span className="st-row-name st-mono" title={`${m.loader}`}>
                    {m.name}
                  </span>
                  <span className="st-row-metric">{m.loader.split('.')[0]}</span>
                </div>
              ))}
              {models.length > 12 && (
                <p className="st-panel-note">… and {models.length - 12} more (search to narrow)</p>
              )}
              {/* WHEN, not just what. Without this the list is undated and reads as current. */}
              {inst?.ts ? (
                <p className="st-panel-note">
                  <Plug size={10} strokeWidth={2} /> read {ago(inst.ts * 1000)}
                  {probe.at ? ` · connection tested ${ago(probe.at)}` : ''}
                </p>
              ) : null}
            </div>
          )}
        </>
      )}
    </section>
  )
}
