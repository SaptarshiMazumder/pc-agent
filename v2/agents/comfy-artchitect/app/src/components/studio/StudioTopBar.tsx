/* The one strip of chrome the studio gets.
 *
 * WHAT IT REPLACES, and why the replacement is smaller. The old toolbar carried a search box, a
 * status chip, a "new run" button and an avatar; below it sat a title, a paragraph of prose, a
 * range switcher and four KPI cards with meters and sparklines. Fifteen surfaces competing before
 * you had looked at a single thing the agent made. None of it was the work.
 *
 * So: a name, and the one live fact that changes what the agent can do — whether the instance
 * answers. Everything else moved into the thing it describes. There is no mode switch either: a
 * render is a file, the rail already lists it, and a second tab to reach it was a tab too many.
 *
 * THE INSTANCE IS A CHIP, NOT A PANEL — but the detail is not lost. GPU, VRAM and the installed
 * model list live in its popover, which is where you go when the answer is "why did it pick fp8".
 * VRAM in particular decides fp8-vs-fp16, so it stays reachable rather than being cut with the
 * rest of the KPI row.
 */

import { RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import type { StudioState } from './useStudioState'
import { useGpuWarmup } from './useGpuWarmup'
import { useInstanceProbe } from './useInstanceProbe'

/** "2 min ago" — a cache is only meaningful with its age attached. */
function ago(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)}h ago`
}

function InstanceChip({ state, client }: { state: StudioState; client?: AgentdClient }) {
  // START THE MACHINE THE MOMENT THE WINDOW OPENS. A cold GPU takes minutes to become
  // reachable, and those minutes otherwise land when the user is waiting on the agent. This
  // moves the wait into the part of the session they spend reading a plan. Idempotent per
  // account, so several windows produce one rental — see useGpuWarmup.
  const gpu = useGpuWarmup(client)
  const probe = useInstanceProbe(client)
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const inst = state.instance

  // Click-away and Escape both close it: a popover you cannot dismiss by looking elsewhere is a
  // panel again, which is the thing being removed.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const models = inst?.models || []
  const vram =
    inst?.vram_total != null
      ? `${((inst.vram_total - (inst.vram_free ?? 0)) / 1e9).toFixed(1)} / ${(inst.vram_total / 1e9).toFixed(1)} GB`
      : null

  return (
    <div className="sb-inst" ref={wrap}>
      <button
        className={`sb-chip is-${probe.state}`}
        onClick={() => setOpen((v) => !v)}
        title="ComfyUI instance"
      >
        <span className="sb-dot" />
        <span>
          {probe.state === 'probing'
            ? 'testing…'
            : probe.state === 'live'
              ? 'instance'
              : // A GPU still coming up is NOT "no instance" — saying so invites the user to go
                // looking for a problem that is a boot in progress. It is the honest label for
                // the first few minutes of every session now that the machine is pre-warmed.
                gpu.state === 'starting'
                ? 'starting GPU…'
                : gpu.state === 'waiting'
                  ? 'waiting for a GPU…'
                  : probe.state === 'down'
                  ? 'no instance'
                  : 'instance'}
        </span>
      </button>

      {open && (
        <div className="sb-pop" role="dialog">
          <div className="sb-pop-head">
            <span className="sb-pop-title">ComfyUI</span>
            <button
              className="sb-test"
              onClick={probe.test}
              disabled={probe.state === 'probing' || !client}
            >
              <RefreshCw
                size={12}
                strokeWidth={2}
                className={probe.state === 'probing' ? 'spin' : ''}
              />
              <span>{probe.state === 'probing' ? 'testing…' : 'test connection'}</span>
            </button>
          </div>

          {probe.state === 'down' ? (
            /* THE FAILURE, IN THE TOOL'S OWN WORDS — and no fix for the user to apply, because
               there is none: the GPU is started for them, and there is no URL or setting to
               correct. A box still booting is the common case here, and it clears itself. */
            <>
              <p className="sb-err st-mono">{probe.error || 'the instance did not answer'}</p>
              <p className="sb-note">
                The GPU is started for you and usually takes a few minutes to answer. Nothing to
                set or paste — if it stays down, the agent will say so in the chat.
              </p>
            </>
          ) : (
            <>
              {probe.state === 'live' && (
                <p className="sb-line st-mono">{probe.detail.split('\n')[0]}</p>
              )}
              {inst?.gpu && <p className="sb-line st-mono">{inst.gpu}</p>}
              {vram && <p className="sb-line st-mono">VRAM {vram}</p>}
              {models.length > 0 ? (
                <>
                  <p className="sb-note">{models.length} model(s) installed</p>
                  <div className="sb-models">
                    {models.slice(0, 10).map((m) => (
                      <span key={`${m.loader}/${m.name}`} className="sb-model st-mono">
                        {m.name}
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <p className="sb-note">
                  {probe.state === 'live'
                    ? 'Connected — no models installed yet; the agent installs what a workflow needs.'
                    : 'Not probed yet — press “test connection”, or just ask for a workflow.'}
                </p>
              )}
              {inst?.ts ? <p className="sb-note">read {ago(inst.ts * 1000)}</p> : null}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function StudioTopBar({
  state,
  client,
  credits,
  onCredits,
}: {
  state: StudioState
  client?: AgentdClient
  credits: number | null
  onCredits: () => void
}) {
  return (
    <header className="sb">
      <span className="sb-name">Workspace</span>

      <span className="sb-spacer" />

      <InstanceChip state={state} client={client} />

      <button className="sb-credits" onClick={onCredits} title="Credits">
        {credits != null ? credits.toLocaleString() : '—'}
      </button>
    </header>
  )
}
