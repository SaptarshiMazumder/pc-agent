/**
 * The SHELL's CanvasHost — binds the shared canvas seam (canvas/host.tsx) to this renderer's
 * own store and gateway. Mounted once around the app (App.tsx), so every canvas surface —
 * the Canvas panel, entity-page workspace trees — sees one consistent host. An agent app
 * never uses this file; it brings its own host built on the SDK client (@agentd/canvas).
 */

import type { JSX, ReactNode } from 'react'

import { gateway } from '../gateway/client'
import type { Artifact } from '../lib/artifacts'
import { fileUrl } from '../lib/artifacts'
import { CanvasHostProvider, type CanvasHost } from '../canvas/host'
import { useApp } from '../state/store'

export default function ShellCanvasHost({ children }: { children: ReactNode }): JSX.Element {
  const connection = useApp((s) => s.connection)
  const artifactActions = useApp((s) => s.artifactActions)
  const busyMap = useApp((s) => s.artifactActionBusy)
  const runArtifactAction = useApp((s) => s.runArtifactAction)
  const openCanvas = useApp((s) => s.openCanvas)

  const host: CanvasHost = {
    request: (method, params) => gateway.request(method, params),
    fileUrl,
    connection,
    // The shell's send-to-chat is its live composer path: the message lands in the CURRENT
    // conversation, exactly as if typed — read fresh via getState so a long-lived canvas
    // never captures a stale sender.
    sendToChat: (text, attachments) => useApp.getState().sendMessage(text, attachments),
    artifactActions,
    runArtifactAction,
    artifactActionBusy: (path: string) => !!busyMap[path],
    openCanvas: (a: Artifact) => openCanvas(a),
  }
  return <CanvasHostProvider value={host}>{children}</CanvasHostProvider>
}
