/* The story this chat made (its newest), re-read whenever the studio's tick moves. A failed read is
 * returned as `error`, for the studio to show — never hidden. */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import { listStories, type Story } from '../agentd/stories'
import { useApp } from '../state/store'

export function useSessionStory(client: AgentdClient | null, session: string): { story: Story | null; error: string } {
  const tick = useApp((s) => s.studioTick)
  const [state, setState] = useState<{ story: Story | null; error: string }>({ story: null, error: '' })
  useEffect(() => {
    if (!client || !session) return
    let gone = false
    listStories(client)
      .then((all) => {
        if (!gone) setState({ story: all.filter((s) => s.session === session).sort((a, b) => b.updated - a.updated)[0] || null, error: '' })
      })
      .catch((e) => {
        if (!gone) setState({ story: null, error: String((e as Error)?.message || e) })
      })
    return () => {
      gone = true
    }
  }, [client, session, tick])
  return state
}
