/* Is the ComfyUI instance ACTUALLY reachable, right now?
 *
 * WHY THIS EXISTS. The dashboard's instance panel used to render `.studio/state.json` — the last
 * probe/inventory the agent happened to run — and nothing ever expired it. So a box that had
 * stopped, or been replaced by a different one on a new port, still listed yesterday's models as
 * though they were there. The panel was a CACHE presented as a STATUS, which is the one thing a
 * status panel must never be.
 *
 * Liveness is not something telemetry can answer; only a request can. This runs the agent's own
 * `comfy_probe` through `tools.invoke` — no model call, no tokens, the same tool the agent uses —
 * and reports what came back. `comfy_inventory` follows on success, so a successful test also
 * REFRESHES the model list rather than merely blessing a stale one.
 */

import { useCallback, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

/** unknown: never tested this session. probing: in flight. live/down: what the wire said. */
export type ProbeState = 'unknown' | 'probing' | 'live' | 'down'

export interface InstanceProbe {
  state: ProbeState
  /** The instance's own words when it answered — version, GPU, VRAM, queue depth. */
  detail: string
  /** Why it did not answer. Shown verbatim: the failure names the fix (unset URL, 401, refused). */
  error: string
  /** When the last test finished, epoch ms. 0 = never. */
  at: number
  test: () => void
}

export function useInstanceProbe(client: AgentdClient | undefined, auto = true): InstanceProbe {
  const [state, setState] = useState<ProbeState>('unknown')
  const [detail, setDetail] = useState('')
  const [error, setError] = useState('')
  const [at, setAt] = useState(0)

  const test = useCallback(() => {
    if (!client) return
    setState('probing')
    setError('')
    void (async () => {
      try {
        const res = (await client.request('tools.invoke', {
          name: 'comfy_probe',
          params: {},
        })) as { text?: string }
        setDetail(String(res?.text || '').trim())
        setState('live')
        // A live instance is also the moment to re-read what is on it — otherwise "connected"
        // would sit above a model list from a different machine. Failure here is not fatal: the
        // connection is proven either way.
        try {
          await client.request('tools.invoke', { name: 'comfy_inventory', params: {} })
        } catch {
          /* inventory is a refresh, not the verdict */
        }
      } catch (e) {
        // tools.invoke rejects with the tool's own error text — which is what names the fix.
        setError(String((e as Error)?.message || e).trim())
        setDetail('')
        setState('down')
      } finally {
        setAt(Date.now())
      }
    })()
  }, [client])

  // Test once when the window opens, so the panel is honest before anyone clicks. Cheap (one
  // HTTP call to the user's own box) and it is the only way the panel can open telling the truth.
  useEffect(() => {
    if (auto && client) test()
  }, [auto, client, test])

  return { state, detail, error, at, test }
}
