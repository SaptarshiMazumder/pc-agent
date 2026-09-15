/* Start this user's GPU the moment the window opens, not when the agent first needs it.
 *
 * WHY EAGER, WHEN LAZY WAS THE EARLIER CHOICE. Lazy is cheaper on paper: research and workflow
 * design need no hardware, so a machine started at session open is idle for the first few
 * minutes. Measured against a real rental, that argument loses to the clock — a cold instance
 * takes MINUTES to become reachable, and those minutes land exactly when the user is waiting to
 * see something happen. Pre-warming moves the wait into the part of the session where they are
 * reading a plan anyway.
 *
 * ONE MACHINE PER ACCOUNT, so this is safe to call from every window. `gpu_ensure` is idempotent
 * by construction: the account's slot is a unique index in the database, so ten windows opening
 * at once produce one rental and nine reads of it.
 *
 * IT NEVER BLOCKS AND NEVER NAGS. A failure here is not the user's problem to solve — they did
 * not ask for a GPU, they opened a chat. The panel says what is happening; the agent still gets
 * a working answer from `gpu_ensure` when it actually needs the machine.
 *
 * THE COST OF BEING WRONG IS A RUNNING GPU, which is why the platform reaps idle instances.
 * Pre-warming only makes sense while that reaper works.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

/** 'waiting' = the platform could not rent one THIS minute (no offer under its filters, or its
 *  machine limit) and said so as a temporary refusal. Polled like 'starting', only slower —
 *  distinct from 'unavailable', which is a refusal time will not fix and is not polled. */
export type GpuState = 'idle' | 'starting' | 'waiting' | 'ready' | 'unavailable'

export interface GpuWarmup {
  state: GpuState
  /** The instance's address once it answers. "" until then. */
  url: string
  /** The link a person can open: the machine's own ComfyUI, with the portal's login token on
   *  it, so the browser is let in. `url` alone lands on a password page nobody has the
   *  password for — which is what the top bar used to link to. "" until ready. */
  openUrl: string
  /** What the platform said when it could not start one — shown, never acted on. */
  error: string
  hourlyUsd: number
  /** What an hour of this machine costs the person, in credits — the platform's own number,
   *  the one the meter charges by. 0 until known. */
  creditsPerHour: number
  /** Ask again now (the panel's retry). */
  refresh: () => void
}

/** How often to re-ask while a machine is still coming up. A cold pull is minutes, so polling
 *  faster buys nothing but requests. */
const POLL_MS = 20_000
/** How often to re-ask when the marketplace had nothing this minute. Offers appear on the scale
 *  of minutes, and each ask is a search plus possibly a rental attempt — once a minute is
 *  enough to turn a thin market at 15:14 into a GPU at 15:17 with nobody doing anything. */
const WAIT_POLL_MS = 60_000

/** How often to tell the platform the machine is in use while it is ready and the chat is
 *  active. The idle timer is ten minutes; once a minute is plenty and costs one small call. */
const TOUCH_MS = 60_000

/**
 * `active` is the window's word on whether anyone is using the machine: a human in the chat
 * (see useHumanActivity) or a run in progress. While the machine is ready and the chat is
 * active, this hook touches the platform once a minute so the idle reaper leaves it alone. It
 * uses gpu_touch, which only ever TOUCHES — a keepalive that could rent would rent a machine for
 * somebody who merely left a chat open.
 */
export function useGpuWarmup(
  client: AgentdClient | undefined,
  enabled = true,
  active = false,
): GpuWarmup {
  const [state, setState] = useState<GpuState>('idle')
  const [url, setUrl] = useState('')
  const [openUrl, setOpenUrl] = useState('')
  const [error, setError] = useState('')
  const [hourlyUsd, setHourlyUsd] = useState(0)
  const [creditsPerHour, setCreditsPerHour] = useState(0)
  // Survives re-renders so a slow poll cannot be started twice by React's strict double-mount.
  const inFlight = useRef(false)

  const ask = useCallback(() => {
    if (!client || inFlight.current) return
    inFlight.current = true
    void (async () => {
      try {
        const res = (await client.request('tools.invoke', {
          name: 'gpu_ensure',
          params: {},
        })) as {
          text?: string
          details?: {
            ready?: boolean
            url?: string
            open_url?: string
            hourly_usd?: number
            credits_per_hour?: number
            waiting?: boolean
            unavailable?: boolean
            detail?: string
          }
        }
        const d = res?.details || {}
        setHourlyUsd(Number(d.hourly_usd || 0))
        setCreditsPerHour(Number(d.credits_per_hour || 0))
        if (d.ready && d.url) {
          setUrl(String(d.url))
          setOpenUrl(String(d.open_url || ''))
          setState('ready')
          setError('')
        } else if (d.waiting) {
          // The platform could not rent one this minute and said it is temporary. Shown, and
          // asked again — this is the case that used to end as 'unavailable' and stop polling,
          // which left the session without a GPU unless the model happened to try again.
          setError(String(d.detail || '').trim())
          setState('waiting')
        } else if (d.unavailable) {
          // No GPU service on this deployment — a desktop daemon, or a platform without the
          // module. Said as a STATE rather than thrown as an error, so the agent designs on
          // regardless (a thrown error read as "the job is blocked"). Nothing to poll for.
          setError(String(d.detail || '').trim())
          setState('unavailable')
        } else {
          // "starting" is the normal answer for the first few minutes, not a failure.
          setState('starting')
          setError('')
        }
      } catch (e) {
        // A deployment with no GPU service says so here. Recorded, not retried into the ground:
        // the agent is perfectly able to design a workflow without hardware.
        setError(String((e as Error)?.message || e).trim())
        setState('unavailable')
      } finally {
        inFlight.current = false
      }
    })()
  }, [client])

  useEffect(() => {
    if (!enabled || !client) return
    ask()
  }, [enabled, client, ask])

  useEffect(() => {
    // Poll ONLY while something is still coming up — booting, or waiting for the market. Once
    // ready — or once the platform has said it cannot — there is nothing a timer can learn, and
    // an idle window should cost nothing.
    if (state !== 'starting' && state !== 'waiting') return
    const t = setInterval(ask, state === 'waiting' ? WAIT_POLL_MS : POLL_MS)
    return () => clearInterval(t)
  }, [state, ask])

  useEffect(() => {
    // THE HEARTBEAT. Ready machine, active chat: once a minute, say so. The moment the chat goes
    // quiet — no human, no run — this stops, and ten minutes later the platform reaps the
    // machine. That is the rule, measured by what matters rather than by tool names.
    if (state !== 'ready' || !active || !client) return
    let stopped = false
    const touch = async () => {
      try {
        const res = (await client.request('tools.invoke', { name: 'gpu_touch', params: {} })) as {
          details?: { alive?: boolean }
        }
        if (!stopped && res?.details?.alive === false) {
          // The machine is gone (reaped, or failed). Say "no instance"; the next run's
          // gpu_ensure starts a fresh one. Not 'starting' — nothing is starting.
          setUrl('')
          setOpenUrl('')
          setState('idle')
        }
      } catch {
        // A deployment without the tool or the service: nothing to keep alive. Stop asking.
        stopped = true
      }
    }
    void touch()
    const t = setInterval(() => void touch(), TOUCH_MS)
    return () => {
      stopped = true
      clearInterval(t)
    }
  }, [state, active, client])

  return { state, url, openUrl, error, hourlyUsd, creditsPerHour, refresh: ask }
}
