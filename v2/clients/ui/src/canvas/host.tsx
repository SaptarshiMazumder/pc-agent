/**
 * CanvasHost — the seam that makes the canvas SHAREABLE.
 *
 * The canvas components (canvasViewers, FabricEditor, WorkspaceTree) are the shell's real
 * editor, and agent apps (Bio Figure) must mount the SAME components — a hand-ported copy is
 * two canvases drifting apart forever. What stopped that was module-level coupling: the
 * components imported the shell's store and gateway singletons directly, so bundling them for
 * an app dragged the whole shell state along.
 *
 * This context is everything the canvas actually NEEDS from whoever hosts it, stated as an
 * interface: the shell provides it from its store/gateway (ShellCanvasHost), an agent app
 * provides it from its own SDK client (@agentd/canvas mount). The components import ONLY this
 * file — never the store, never the gateway singleton.
 */

import { createContext, useContext } from 'react'

import type { Artifact, ArtifactAction } from '../lib/artifacts'

export interface ChatAttachment {
  name: string
  mimeType: string
  dataBase64: string
}

export interface CanvasHost {
  /** One RPC to the daemon this host is connected to (workspace.*, tools.invoke, …). */
  request<T = Record<string, unknown>>(method: string, params?: Record<string, unknown>): Promise<T>
  /** Absolute, authorized URL for an artifact's bytes (the /file endpoint). */
  fileUrl(path: string): string
  /** Socket state — the tree reloads on 'open'. 'idle' = not yet dialed (shell boot). */
  connection: 'idle' | 'connecting' | 'open' | 'closed'
  /** Send a user message with attachments into the host's conversation (send-to-chat). */
  sendToChat(text: string, attachments: ChatAttachment[]): Promise<void>
  /** Self-declared artifact actions (plugins.catalog) + how to run one and whether one runs. */
  artifactActions: ArtifactAction[]
  runArtifactAction(action: ArtifactAction, artifact: Artifact): Promise<void>
  artifactActionBusy(path: string): boolean
  /** Open an artifact in the host's canvas surface (tree click-through). */
  openCanvas(artifact: Artifact): void
}

const Ctx = createContext<CanvasHost | null>(null)

export const CanvasHostProvider = Ctx.Provider

export function useCanvasHost(): CanvasHost {
  const host = useContext(Ctx)
  if (!host) {
    // Loud and immediate: a canvas rendered outside a provider would otherwise fail on the
    // first click with something unrelated-looking.
    throw new Error('CanvasHost missing — wrap canvas components in a <CanvasHostProvider>')
  }
  return host
}
