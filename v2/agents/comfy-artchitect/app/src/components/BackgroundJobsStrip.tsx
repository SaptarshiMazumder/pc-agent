/* The tool calls that left the turn and are still working — one row each, only while there are any.
 *
 * WHAT THIS ANSWERS. A long tool call (validate holding until a download lands) no longer holds
 * the conversation: the daemon takes it off the turn as a background job, the model gets told,
 * and the composer unlocks. Without this strip the wait would be invisible again — the tool row
 * in the thread is already closed with "continues as job j1" — so this is where the wait lives:
 * what is being waited on, its latest progress line, how long, and a way to stop it.
 *
 * THE PROGRESS LINE IS THE TOOL'S OWN, relayed live from the microVM. "waiting for ComfyUI-
 * Manager to finish downloading (0/1 done)" is what comfy_validate reports; nothing here invents
 * a percentage. The clock ticks locally from the daemon's start stamp.
 *
 * CANCEL STOPS ONE JOB and nothing else — the turn, if one is going, keeps going. Stop (in the
 * composer) is the bigger hammer: it kills the turn and every job.
 */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import type { BackgroundJob } from '../state/store'

/** `4m12s`, `37s` — the same spelling the daemon's own messages use. */
function clock(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

export function BackgroundJobsStrip({
  jobs,
  sessionKey,
  client,
}: {
  jobs: BackgroundJob[]
  sessionKey: string
  client: AgentdClient | null
}) {
  const [now, setNow] = useState(() => Date.now())
  const [stopping, setStopping] = useState<string>('')

  // A clock only while there is something to time.
  useEffect(() => {
    if (!jobs.length) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [jobs.length])

  if (!jobs.length) return null

  const cancel = async (jobId: string): Promise<void> => {
    if (!client || stopping) return
    setStopping(jobId)
    try {
      await client.request('jobs.cancel', { sessionKey, jobId })
    } catch {
      /* the job may have just ended on its own; the strip follows the daemon's events */
    } finally {
      setStopping('')
    }
  }

  return (
    <div className="bj" role="status" aria-live="polite">
      {jobs.map((j) => (
        <div className="bj-row" key={j.id}>
          <span className="bj-dot" />
          <span className="bj-tool">{j.tool}</span>
          <span className="bj-text" title={j.text}>
            {j.text || 'working in the background…'}
          </span>
          <span className="bj-meta">{clock(now - j.startedAt)}</span>
          <button
            className="bj-cancel"
            onClick={() => void cancel(j.id)}
            disabled={!client || stopping === j.id}
            title="Stop this job. The conversation keeps going."
          >
            {stopping === j.id ? 'stopping…' : 'Cancel'}
          </button>
        </div>
      ))}
    </div>
  )
}
