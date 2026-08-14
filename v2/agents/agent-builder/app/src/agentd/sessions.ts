/* The conversation list in the rail. */

import type { AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'
import { AGENT_ID, useDaemonEvent } from './client'

export interface ChatRow {
  sessionId: string
  title?: string
  snippet?: string
  messages?: number
  /** Unix SECONDS, not milliseconds — see `when`. */
  modified?: number
}

/** Recent-first relative time. "3h ago" answers "is this the one I was just in?"; a timestamp
 *  makes you work it out yourself.
 *
 *  The daemon sends SECONDS. Feeding that to `new Date()` unmultiplied dates every conversation
 *  to January 1970, which sorts and reads as if nothing was ever touched. */
export function when(ts?: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const days = (Date.now() - d.getTime()) / 86400000
  if (days < 1) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (days < 7) return d.toLocaleDateString([], { weekday: 'short' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

/** Conversations with Agent Builder, kept current with no polling.
 *
 *  `sessions.changed` is the event that matters here: a new chat gets its auto-title a moment
 *  AFTER the first exchange, so a list refreshed only on send would show "Untitled" until the
 *  next reload. */
export function useSessions(client: AgentdClient, ready: boolean) {
  const [chats, setChats] = useState<ChatRow[]>([])

  const reload = useCallback(async () => {
    try {
      const res = await client.sessions(AGENT_ID)
      setChats((res?.sessions as ChatRow[]) || [])
    } catch {
      // Advisory only — the chat itself works without its own history list.
      setChats([])
    }
  }, [client])

  useDaemonEvent(client, 'sessions.changed', () => void reload())

  // Gated on the socket being OPEN — see the same note in useAgents.
  useEffect(() => {
    if (ready) void reload()
  }, [ready, reload])

  return { chats, reload }
}
