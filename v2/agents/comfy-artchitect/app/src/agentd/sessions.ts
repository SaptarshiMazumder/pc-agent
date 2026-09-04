/* Conversations with Agent Builder: the row shape the rail renders, and the operations you can
 * perform on one.
 *
 * THE LIST ITSELF LIVES IN THE STORE (state/store.ts), with the `sessions.changed` subscription
 * that keeps it current. It moved there so the copied sidebar components can read it the way
 * agentd's do; what stays here is everything that is about a session rather than about the list. */

import type { AgentdClient } from '@agentd/client'
import { AGENT_ID } from './client'
import { restore } from './chat'
import type { ThreadItem } from './chat'

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

/**
 * Give a conversation a name of your own.
 *
 * AN EMPTY TITLE IS NOT A NO-OP — it is how you take a manual name back off, and the daemon
 * treats it that way: it clears the `manual` flag so auto-titling resumes from the transcript.
 * So nothing here guards against a blank; refusing to send one would quietly remove the only way
 * to undo a rename.
 *
 * The daemon broadcasts `sessions.changed`, which is what refreshes every open client's list —
 * including this one. Writing the new title into local state as well would race that broadcast
 * and let the row flicker between the two answers.
 */
/** This agent's saved conversations. The daemon scopes the list to this agent, so nothing here
 *  filters — and the rows come back in the shape `ChatRow` above already describes, because the
 *  sidebar, the rename and the fork all read the same one. */
export async function listSessions(client: AgentdClient): Promise<ChatRow[]> {
  const res: any = await client.request('sessions.list', { agentId: AGENT_ID })
  const rows: any[] = res?.sessions || res?.rows || []
  return rows.map((r) => ({
    sessionId: String(r.sessionId || r.sessionKey || r.key || ''),
    title: r.title ? String(r.title) : undefined,
    snippet: r.snippet ? String(r.snippet) : undefined,
    messages: Number(r.messages || 0),
    modified: Number(r.modified || r.updatedAt || 0),
  }))
}

/** One saved conversation's transcript, as the thread items a live run would have produced.
 *
 *  This is what makes a Recent-list click RESUME the chat instead of opening a blank one:
 *  `openSession` only switches the key and seeds an empty session (a live run may still be
 *  going, so it must not clobber), and nothing was ever fetching the history behind it. The
 *  daemon's `sessions.history` returns wire-form messages; `restore` folds them into the same
 *  items the transcript renders. */
export async function loadHistory(
  client: AgentdClient,
  sessionId: string,
): Promise<ThreadItem[]> {
  const res: any = await client.request('sessions.history', {
    agentId: AGENT_ID,
    sessionKey: sessionId,
  })
  return restore(res?.messages || [])
}

export async function renameSession(
  client: AgentdClient,
  sessionKey: string,
  title: string,
): Promise<void> {
  const res: any = await client.request('sessions.rename', {
    agentId: AGENT_ID,
    sessionKey,
    title,
  })
  // Reported, never swallowed — same reason as the fork below.
  if (!res?.ok) {
    throw new Error(String(res?.error || 'the daemon would not rename this conversation'))
  }
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
