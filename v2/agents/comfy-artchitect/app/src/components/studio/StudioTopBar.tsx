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

import { ChevronDown, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import { useLibraryFlash } from '../../state/use-library-flash'
import type { StudioState } from './useStudioState'
import type { GpuWarmup } from './useGpuWarmup'
import { OpenComfyButton, type EngineReadiness } from './OpenComfyButton'
import { connectionTitle } from './ConnectionSection'
import type { ComfyConnection } from './useComfyConnection'
import { useInstanceProbe } from './useInstanceProbe'

/** "2 min ago" — a cache is only meaningful with its age attached. */
function ago(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)}h ago`
}

function InstanceChip({
  state,
  client,
  gpu,
  connection,
}: {
  state: StudioState
  client?: AgentdClient
  /** The window's one GPU poller — owned by App, which also resumes a waiting turn from it. */
  gpu: GpuWarmup
  /** Where the account's ComfyUI runs — the rented GPU, the person's own, or not chosen yet. */
  connection: ComfyConnection
}) {
  const own = connection.kind === 'user_vast' || connection.kind === 'user_url'
  const probe = useInstanceProbe(client, connection.kind ? `${connection.kind}:${connection.label}` : '')
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

  /* ONE CONTROL FOR THE ENGINE: the left of it opens ComfyUI (loud once it answers, disabled
     and saying why before), the caret on the right opens the details that used to be the chip's.
     A GPU still coming up is NOT "offline" — it is the honest label for the first minutes of
     every session now that the machine is pre-warmed. */
  // ON THE PERSON'S OWN MACHINE the probe alone decides — a rented GPU another chat keeps
  // warm says nothing about it.
  const ready = probe.state === 'live' || (!own && gpu.state === 'ready')
  const engine: EngineReadiness = ready
    ? { ready: true, label: own ? `${connectionTitle(connection.kind)} ready` : 'Engine ready', pending: false }
    : !connection.kind
      ? { ready: false, label: connection.loaded ? 'Choose where to run' : 'Checking engine…', pending: !connection.loaded }
    : own
      ? probe.state === 'probing'
        ? { ready: false, label: 'Checking your ComfyUI…', pending: true }
        : { ready: false, label: 'Your ComfyUI is not answering', pending: false }
    : gpu.state === 'starting'
      ? { ready: false, label: 'Engine starting…', pending: true }
      : gpu.state === 'waiting'
        ? { ready: false, label: 'Waiting for a GPU…', pending: true }
        : probe.state === 'probing'
          ? { ready: false, label: 'Checking engine…', pending: true }
          : { ready: false, label: 'Engine offline', pending: false }

  return (
    <div className="sb-inst" ref={wrap}>
      <div className={`eng${ready ? ' is-live' : ''}`}>
        <OpenComfyButton client={client} engine={engine} ownUrl={own ? connection.label : ''} />
        <button
          className="eng-more"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label="Engine details"
          title="Engine details: GPU, VRAM, installed models, test the connection"
        >
          <ChevronDown size={14} strokeWidth={2.2} />
        </button>
      </div>

      {open && (
        <div className="sb-pop" role="dialog">
          <div className="sb-pop-head">
            <span className="sb-pop-title">Engine · ComfyUI</span>
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

          {!own && gpu.state === 'ready' && gpu.url && (
            /* THE LINK THE USER ASKED FOR: their instance, in a new tab — ComfyUI's own web UI
               on the rented machine, the one place they can watch a queue and outputs
               directly. The Vast console is the platform's account, not theirs, so it is not
               offered. `openUrl` carries the portal's login token, so the tab opens ComfyUI
               itself; the bare `url` opened a password page nobody had the password for. */
            <a
              className="sb-link st-mono"
              href={gpu.openUrl || gpu.url}
              target="_blank"
              rel="noreferrer"
            >
              open ComfyUI ↗
            </a>
          )}
          {!own && gpu.state === 'ready' && gpu.creditsPerHour > 0 && (
            /* WHAT IT COSTS, where the thing that costs it is. The person's credits pay for the
               machine by the minute now; a rate they can see is the difference between a bill
               and a surprise. */
            <p className="sb-note">
              about {gpu.creditsPerHour.toLocaleString()} credits an hour while it runs
            </p>
          )}
          {!own && gpu.state === 'waiting' && (
            <p className="sb-note">{gpu.error || 'no GPU free this minute — asking again'}</p>
          )}

          {probe.state === 'down' ? (
            /* THE FAILURE, IN THE TOOL'S OWN WORDS — and no fix for the user to apply, because
               there is none: the GPU is started for them, and there is no URL or setting to
               correct. A box still booting is the common case here, and it clears itself. */
            <>
              <p className="sb-err st-mono">{probe.error || 'the instance did not answer'}</p>
              <p className="sb-note">
                {own
                  ? 'Check that your ComfyUI is running — or switch to a rented GPU in Workspace → Connection.'
                  : 'The GPU is started for you and usually takes a few minutes to answer — if it stays down, the agent will say so in the chat.'}
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

/** The stage's two tabs: this chat's own files, and the Library every chat shares. */
export type StudioPanel = 'workspace' | 'library'

export function StudioTopBar({
  state,
  client,
  gpu,
  credits,
  onCredits,
  panel,
  onPanel,
  attention = false,
  connection,
}: {
  state: StudioState
  client?: AgentdClient
  gpu: GpuWarmup
  connection: ComfyConnection
  credits: number | null
  onCredits: () => void
  panel: StudioPanel
  onPanel: (p: StudioPanel) => void
  /** The Workspace holds an input the agent is waiting on. */
  attention?: boolean
}) {
  const flash = useLibraryFlash()
  return (
    <header className="sb">
      {/* TWO TABS, TWO SCOPES. Workspace is this chat's — its inputs, renders, workflow and
          files. Library is everyone's — what the person chose to keep, reachable from any chat. */}
      <div className="sb-tabs" role="tablist" aria-label="Studio panel">
        <button
          type="button"
          role="tab"
          aria-selected={panel === 'workspace'}
          className={`sb-tab${panel === 'workspace' ? ' on' : ''}${attention ? ' is-attention' : ''}`}
          onClick={() => onPanel('workspace')}
          title="This chat's inputs, renders, workflow and files"
        >
          Workspace
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={panel === 'library'}
          className={`sb-tab${panel === 'library' ? ' on' : ''}${flash ? ' is-flash' : ''}`}
          onClick={() => onPanel('library')}
          title="What you kept — shared by every chat"
        >
          Library
        </button>
      </div>

      <span className="sb-spacer" />

      <InstanceChip state={state} client={client} gpu={gpu} connection={connection} />

      <button className="sb-credits" onClick={onCredits} title="Credits">
        {credits != null ? `${credits.toLocaleString()} cr` : '—'}
      </button>
    </header>
  )
}
