/* What the user's ComfyUI actually holds — the last probe + inventory, verbatim. A model row
 * lights up when it is the one the current/last run loaded. No sizes: the HTTP API does not
 * report them, and a made-up "6.9 GB" would be worse than none. */

import type { StudioState } from './useStudioState'

export function InstancePanel({ state, query }: { state: StudioState; query: string }) {
  const inst = state.instance
  const q = query.trim().toLowerCase()
  const models = (inst?.models || []).filter(
    (m) => !q || m.name.toLowerCase().includes(q) || m.loader.toLowerCase().includes(q),
  )
  const activeCkpt =
    state.active?.checkpoint || (state.runs || [])[0]?.checkpoint || ''

  return (
    <section className="st-panel">
      <div className="st-panel-head">
        <h2>On this instance</h2>
        {inst?.version && <span className="st-panel-note st-mono">ComfyUI {inst.version}</span>}
      </div>
      {inst?.gpu && <p className="st-instance-gpu st-mono">{inst.gpu}</p>}
      {models.length === 0 ? (
        <p className="st-empty">
          {inst ? 'No inventory yet — ask the agent what is installed.' : 'Not probed yet — the first ComfyUI question fills this in.'}
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
        </div>
      )}
    </section>
  )
}
