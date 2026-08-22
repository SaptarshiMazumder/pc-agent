/* How full this conversation's context is.
 *
 * WHY IT IS ON SCREEN AT ALL. A conversation that outgrows its model does not fail in a way anyone
 * can read: the provider returns an empty response, the runtime retries by appending ANOTHER
 * message and re-sending, and the same "couldn't generate a response" appears twice with nothing
 * to explain it. The number that explains it exists the whole time — it just was not shown.
 *
 * THE NUMBERS ARE THE DAEMON'S, not ours. `used` is what the provider actually BILLED for the last
 * request; `limit` comes from the model's own table; `pct` is computed server-side so two windows
 * cannot round it differently. Nothing here estimates anything — an estimate that disagreed with
 * the real wall would be worse than no meter.
 *
 * SILENCE IS A VALID STATE. The daemon says nothing when either half is unknown (an unfamiliar
 * model, a turn that billed nothing), and this hook stays null rather than inventing a denominator
 * — a guessed limit would show a full ring on an empty chat.
 */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useRef, useState } from 'react'

export interface ContextUsage {
  used: number
  limit: number
  /** 0..1, already rounded by the daemon. */
  pct: number
  model: string
  /** The cached subset of `used` — why a large context can still be cheap. */
  cached: number
}

/** Past this, the ring warns. Chosen so there is room to finish a thought AND compact. */
export const WARN_AT = 0.6
/** Past this, compaction is the recommended action rather than an option. */
export const CROWDED_AT = 0.8

export function usageTone(pct: number): 'ok' | 'warn' | 'crowded' {
  if (pct >= CROWDED_AT) return 'crowded'
  if (pct >= WARN_AT) return 'warn'
  return 'ok'
}

/** "47.2k" — a ring has no room for six digits, and the exact figure is in the tooltip. */
export function compactTokens(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/**
 * The live meter for the open conversation.
 *
 * Reset by the caller when the conversation changes — a new chat starts empty, and leaving the
 * previous chat's number on screen would be a lie about the one you are now in.
 */
export function useContextUsage(client: AgentdClient, sessionKey: string) {
  const [usage, setUsage] = useState<ContextUsage | null>(null)

  // Read through a ref inside the handler: the subscription is opened ONCE, so a value captured
  // in its closure would keep filtering against whichever conversation was open when it was
  // created — the same stale-closure trap the file tree hit.
  const openKey = useRef(sessionKey)
  openKey.current = sessionKey

  // A new conversation has no measurement yet. Clearing on the key change rather than waiting for
  // the first reply means the ring empties when you press New, which is what actually happened.
  useEffect(() => setUsage(null), [sessionKey])

  useEffect(() => {
    // `chat.event` carries every run event; `context_usage` is one of them (APP_FACING_EVENTS).
    const off = client.on('chat.event', (frame: any) => {
      // ONE SOCKET, EVERY CONVERSATION. Without this filter a run in another session — a
      // background job, a second window — would repaint this ring with a number belonging to a
      // conversation the user is not looking at.
      if (frame?.sessionKey !== openKey.current) return
      const ev = frame?.event
      if (ev?.type !== 'context_usage') return
      setUsage({
        used: Number(ev.used || 0),
        limit: Number(ev.limit || 0),
        pct: Number(ev.pct || 0),
        model: String(ev.model || ''),
        cached: Number(ev.cached || 0),
      })
    })
    return off
  }, [client])

  return usage
}
