/**
 * The SHELL's SessionsHost — binds the chat-row seam (chat/sessionsHost.tsx) to this renderer's
 * store. Mounted once around the app beside ShellCanvasHost and ShellChatHost, so every list that
 * renders a chat row — sidebar Recents, the project page, the agent page — sees one host.
 * An agent app never uses this file; it brings its own, built on the SDK client.
 */

import type { JSX, ReactNode } from 'react'

import { SessionsHostProvider, type SessionsHost } from '../chat/sessionsHost'
import { useApp } from '../state/store'

export default function ShellSessionsHost({ children }: { children: ReactNode }): JSX.Element {
  const renameSession = useApp((s) => s.renameSession)
  const deleteSession = useApp((s) => s.deleteSession)
  const moveSession = useApp((s) => s.moveSession)
  const duplicateSession = useApp((s) => s.duplicateSession)
  const exportSessionMd = useApp((s) => s.exportSessionMd)
  const projects = useApp((s) => s.projects)
  const agents = useApp((s) => s.agents)
  const agentName = useApp((s) => s.hello?.agentName)

  const host: SessionsHost = {
    renameSession,
    deleteSession,
    moveSession,
    duplicateSession,
    exportSessionMd,
    projects,
    agents,
    agentName
  }
  return <SessionsHostProvider value={host}>{children}</SessionsHostProvider>
}
