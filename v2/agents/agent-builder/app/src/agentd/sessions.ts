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

/**
 * Fork a conversation: a full copy — transcript, title, project — under a new key.
 *
 * The daemon already does the work (`sessions.duplicate` copies the transcript and its meta and
 * announces the new session), so this is the address of it and nothing more.
 *
 * WHY THE AGENT SCOPE COMES BACK FOR FREE. Opening the copy replays its transcript, and the
 * subject is read back out of that (`subjectOf` in chat.ts): a scoped chat carries its preamble
 * in message one, and one that BUILT something carries the `create_agent` call that named it. So
 * the fork lands pointed at the same agent without anything here having to know which.
 */
export async function forkSession(client: AgentdClient, sessionKey: string): Promise<string> {
  const res: any = await client.request('sessions.duplicate', {
    agentId: AGENT_ID,
    sessionKey,
  })
  // Reported, never swallowed: a fork button that silently does nothing leaves the user unsure
  // whether they now have two conversations or one.
  if (!res?.ok || !res?.sessionKey) {
    throw new Error(String(res?.error || 'the daemon would not copy this conversation'))
  }
  return String(res.sessionKey)
}
