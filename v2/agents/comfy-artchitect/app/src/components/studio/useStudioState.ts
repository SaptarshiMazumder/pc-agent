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
}

const POLL_MS = 5_000

export function useStudioState(client: AgentdClient | undefined, running: boolean): StudioState {
  const [state, setState] = useState<StudioState>({})
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
    const t = setInterval(pull, POLL_MS)
    return () => {
      stop = true
      clearInterval(t)
    }
    // `running` in the deps on purpose: a run starting or ending is the moment the state is
    // most likely to have changed, so flipping it re-pulls immediately instead of on the tick.
  }, [client, running])

  return state
}
