/* The dashboard's data feed: the bridge's `.studio/state.json`, polled through the one channel
 * a window has to its agent's tools (`tools.invoke` → `comfy_studio_state`, structured
 * `details`). Polling, not a stream: the file changes at tool-call cadence (seconds), the
 * daemon has no watch API for it, and 5s of staleness on telemetry is invisible next to a
 * 6-second render.
 */

import { useEffect, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

export interface StudioRun {
  name: string
  checkpoint: string
  steps: number | null
  duration: number
  status: 'complete' | 'failed' | 'interrupted' | string
  outputs: number
  ts: number
}

export interface StudioRender {
  path: string
  filename: string
  w?: number
  h?: number
  bytes?: number
  ts: number
}

export interface StudioState {
  instance?: {
    version?: string
    gpu?: string
    vram_free?: number
    vram_total?: number
    models?: { loader: string; name: string }[]
    ts?: number
  }
  active?: {
    workflow: string
    prompt_id: string
    checkpoint: string
    steps: number | null
    started: number
    elapsed: number
    status: string
  } | null
  runs?: StudioRun[]
  renders?: StudioRender[]
  /** Model downloads and node-pack installs (studio_state.set_install_progress). */
  installs?: { models?: StudioInstall[]; node_packs?: StudioInstall[] }
}

export interface StudioInstall {
  name: string
  /** starting | downloading | retrying | verifying | installing | done | failed */
  state: string
  received?: number | null
  total?: number | null
  bytes_per_second?: number | null
  error?: string | null
  /** Seconds since the epoch — the plugin's clock. */
  updated_at: number
}

const POLL_MS = 5_000
/** While something installs, its bar moves every two seconds rather than every five. */
const INSTALL_POLL_MS = 2_000
const ENDED = new Set(['done', 'failed'])

/** Is anything still installing? A row not updated for 3 minutes has stopped reporting. */
export function installing(state: StudioState): boolean {
  const now = Date.now() / 1000
  return [...(state.installs?.models || []), ...(state.installs?.node_packs || [])].some(
    (r) => !ENDED.has(r.state) && now - r.updated_at < 180,
  )
}

/* ONE POLLER for the whole window: App calls this once and hands the state to the dashboard and
   to the install panel above the composer. */
export function useStudioState(client: AgentdClient | undefined, running: boolean): StudioState {
  const [state, setState] = useState<StudioState>({})
  const fast = installing(state)
  // The poll must not stack requests when one is slow — one in flight, ever.
  const busy = useRef(false)

  useEffect(() => {
    if (!client) return
    let stop = false

    const pull = async () => {
      if (busy.current) return
      busy.current = true
      try {
        const res = (await client.request('tools.invoke', {
          name: 'comfy_studio_state',
          params: {},
        })) as { details?: StudioState }
        if (!stop && res?.details) setState(res.details)
      } catch {
        /* the daemon is away or the tool is mid-reload — the dashboard keeps its last state,
           which is exactly what a telemetry panel should do */
      } finally {
        busy.current = false
      }
    }

    void pull()
    const t = setInterval(pull, fast ? INSTALL_POLL_MS : POLL_MS)
    return () => {
      stop = true
      clearInterval(t)
    }
    // `running` in the deps on purpose: a run starting or ending is the moment the state is
    // most likely to have changed, so flipping it re-pulls immediately instead of on the tick.
  }, [client, running, fast])

  return state
}
