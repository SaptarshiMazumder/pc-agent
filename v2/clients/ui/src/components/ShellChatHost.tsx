/**
 * The SHELL's ChatHost — binds the shared chat seam (chat/host.tsx) to this renderer's store.
 * Mounted once around the app (App.tsx) beside ShellCanvasHost, so every rendered message —
 * the live thread, a resumed transcript — sees one consistent host. An agent app never uses
 * this file; it brings its own host built on the SDK client (@agentd/canvas).
 */

import type { JSX, ReactNode } from 'react'

import { ChatHostProvider, type ChatHost } from '../chat/host'
import { useApp } from '../state/store'

export default function ShellChatHost({ children }: { children: ReactNode }): JSX.Element {
  const running = useApp((s) => s.sessions[s.currentSessionKey]?.running ?? false)
  const seedComposer = useApp((s) => s.seedComposer)
  const openToolConfig = useApp((s) => s.openToolConfig)

  const host: ChatHost = { running, seedComposer, openToolConfig }
  return <ChatHostProvider value={host}>{children}</ChatHostProvider>
}
