/**
 * SessionsHost — what a rendered CHAT ROW needs from whoever is hosting it.
 *
 * The third seam, and the last one the sidebar needs: `SessionItem` (the row, its double-click
 * rename, its ⋯ menu) and `ChatMenu` are the shell's own components, and an agent window renders
 * the same conversations. Without this, an app either does without the ⋯ menu or grows a
 * lookalike — and a lookalike is exactly how "why does this behave differently?" happens: mine
 * had three inline icons where the product has one menu with five items and a two-step delete.
 *
 * WHAT AN APP CANNOT DO is in here too, as data rather than as a branch: `projects` is empty for
 * an app-scoped connection (projects are host-only), and ChatMenu hides "Move to project" when
 * there is nowhere to move to. So the same component covers both hosts without either one knowing
 * which it is — no `isApp` flag anywhere, which is the whole point of passing capability as data.
 */

import { createContext, useContext } from 'react'

import type { AgentInfo, ProjectRow } from '../gateway/protocol'

export interface SessionsHost {
  renameSession(sessionId: string, title: string): Promise<void> | void
  deleteSession(sessionId: string): Promise<void> | void
  duplicateSession(sessionId: string): Promise<void> | void
  /** Move a chat into a project. Never called where `projects` is empty. */
  moveSession(sessionId: string, projectId: string): Promise<void> | void
  /** Export the transcript as Markdown. */
  exportSessionMd(sessionId: string): Promise<void> | void
  /** Projects to offer in the ⋯ menu. `[]` on an app connection — the item then hides itself. */
  projects: ProjectRow[]
  /** Known agents, for the row's agent dot and label in cross-agent lists. */
  agents: AgentInfo[]
  /** The daemon's default agent display name (agentLabel's fallback). */
  agentName?: string
}

const Ctx = createContext<SessionsHost | null>(null)

export const SessionsHostProvider = Ctx.Provider

export function useSessionsHost(): SessionsHost {
  const host = useContext(Ctx)
  if (!host) {
    throw new Error('SessionsHost missing — wrap chat rows in a <SessionsHostProvider>')
  }
  return host
}
