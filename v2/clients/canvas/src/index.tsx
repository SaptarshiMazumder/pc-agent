/**
 * @agentd/canvas — the agentd client's REAL canvas, mountable in an agent app.
 *
 * Not a lookalike: this bundles the shell's own components (../../ui/src) — viewers,
 * the fabric annotate/vector/PNG editor, the workspace tree — behind the CanvasHost seam
 * (ui/src/canvas/host.tsx). The shell feeds that seam from its store; here it is fed from
 * the app's SDK client. One canvas, two hosts, zero drift.
 *
 *   const canvas = agentdCanvas.mountCanvas(el, { client, sendToChat })
 *   canvas.open({ path, name, mime, kind: 'image' })
 *   agentdCanvas.mountWorkspace(treeEl, { client, agentId, onOpen: (a) => canvas.open(a) })
 */

import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { CanvasHostProvider, type CanvasHost, type ChatAttachment } from '../../ui/src/canvas/host'
import type { Artifact, ArtifactAction } from '../../ui/src/lib/artifacts'
import { CanvasBody } from '../../ui/src/components/canvasViewers'
import WorkspaceTree from '../../ui/src/components/WorkspaceTree'

interface MountOptions {
  /** A connected @agentd/client instance (agentd.fromPage()). */
  client: {
    request<T = Record<string, unknown>>(method: string, params?: Record<string, unknown>): Promise<T>
    fileUrl(path: string): string
    onStatus(handler: (s: string) => void): () => void
  }
  /** Which agent's workspace/tools this surface acts on (defaults to the page's scope). */
  agentId?: string
  /** Send-to-chat lands HERE — the app owns its conversation. Required for the editor's Send. */
  sendToChat?: (text: string, attachments: ChatAttachment[]) => Promise<void>
  /** Tree click-through (mountWorkspace): open the file in the app's canvas. */
  onOpen?: (artifact: Artifact) => void
}

/** Artifact actions, the same data-driven way the shell learns them: any enabled tool that
 *  self-declares one (plugins.catalog). No canvas-side hardcoding of tools, ever. */
async function fetchActions(client: MountOptions['client']): Promise<ArtifactAction[]> {
  try {
    const res = await client.request<{
      plugins: { enabled: boolean; tools: { name: string; enabled: boolean; artifactAction?: ArtifactAction | null }[] }[]
    }>('plugins.catalog')
    const out: ArtifactAction[] = []
    for (const p of res.plugins || []) {
      if (!p.enabled) continue
      for (const t of p.tools || []) {
        if (t.enabled && t.artifactAction) out.push({ ...t.artifactAction, tool: t.name })
      }
    }
    return out
  } catch {
    return [] // an app socket without plugins.catalog: viewers work, action buttons just absent
  }
}

function useHost(opts: MountOptions, openCanvas: (a: Artifact) => void): CanvasHost {
  const [connection, setConnection] = useState<CanvasHost['connection']>('connecting')
  const [actions, setActions] = useState<ArtifactAction[]>([])
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  useEffect(() => {
    const off = opts.client.onStatus((s) => {
      setConnection(s === 'open' ? 'open' : s === 'closed' ? 'closed' : 'connecting')
      if (s === 'open') void fetchActions(opts.client).then(setActions)
    })
    return off
  }, [opts.client])
  return {
    request: (m, p) => opts.client.request(m, p),
    fileUrl: (p) => opts.client.fileUrl(p),
    connection,
    sendToChat: async (text, attachments) => {
      if (!opts.sendToChat) throw new Error('this app did not wire sendToChat')
      await opts.sendToChat(text, attachments)
    },
    artifactActions: actions,
    runArtifactAction: async (action, artifact) => {
      setBusy((b) => ({ ...b, [artifact.path]: true }))
      try {
        await opts.client.request('tools.invoke', {
          name: action.tool,
          ...(opts.agentId ? { agentId: opts.agentId } : {}),
          params: { [action.param]: artifact.path },
        })
      } finally {
        setBusy((b) => ({ ...b, [artifact.path]: false }))
      }
    },
    artifactActionBusy: (path) => !!busy[path],
    openCanvas,
  }
}

function CanvasApp({ opts, onApi }: { opts: MountOptions; onApi: (open: (a: Artifact) => void) => void }): JSX.Element {
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const host = useHost(opts, setArtifact)
  useEffect(() => onApi(setArtifact), [onApi])
  return (
    <CanvasHostProvider value={host}>
      {artifact ? (
        <CanvasBody a={artifact} />
      ) : (
        <div className="cv-empty-hint">Open a figure or a workspace file to view and edit it here.</div>
      )}
    </CanvasHostProvider>
  )
}

function TreeApp({ opts }: { opts: MountOptions }): JSX.Element {
  const host = useHost(opts, (a) => opts.onOpen?.(a))
  return (
    <CanvasHostProvider value={host}>
      <WorkspaceTree agentId={opts.agentId || ''} />
    </CanvasHostProvider>
  )
}

export function mountCanvas(el: HTMLElement, opts: MountOptions): { open(a: Artifact): void; unmount(): void } {
  el.classList.add('agentd-canvas-host') // scopes the vendored theme vars to this subtree
  let openFn: (a: Artifact) => void = () => {}
  const root = createRoot(el)
  root.render(<CanvasApp opts={opts} onApi={(open) => { openFn = open }} />)
  return { open: (a) => openFn(a), unmount: () => root.unmount() }
}

export function mountWorkspace(el: HTMLElement, opts: MountOptions): { unmount(): void } {
  el.classList.add('agentd-canvas-host')
  const root = createRoot(el)
  root.render(<TreeApp opts={opts} />)
  return { unmount: () => root.unmount() }
}
