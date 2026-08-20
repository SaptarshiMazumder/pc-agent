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

import { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { CanvasHostProvider, type CanvasHost, type ChatAttachment } from '../../ui/src/canvas/host'
import type { Artifact, ArtifactAction } from '../../ui/src/lib/artifacts'
import { kindLabel } from '../../ui/src/lib/canvasFile'
import { CanvasBody } from '../../ui/src/components/canvasViewers'
import WorkspaceTree from '../../ui/src/components/WorkspaceTree'
import { installSoftScroll } from '../../ui/src/lib/softScroll'
import { ChatSurface, SessionRail, type ChatClient, type OutgoingAttachment } from './chat'
import { AccountFooter, type AccountAdapter } from './account'
import { SettingsPage } from './settings'
import { Download, ExternalLink, PanelLeft, Settings as SettingsIcon, SquarePen, X } from 'lucide-react'

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

/**
 * THE WHOLE PRODUCT WINDOW — conversations on the left, the chat in the middle, the canvas on the
 * right. The agentd shell's layout, its class names and its components, driven by an app's own
 * socket.
 *
 * ONE React root, deliberately. The chat and the canvas have to share a CanvasHost or clicking a
 * figure in a message could not open it in the panel beside it — which is the single interaction
 * this surface exists for. Two roots would mean two hosts and a message whose artifacts open
 * nothing.
 */
function ShellApp({ opts }: { opts: ShellOptions }): JSX.Element {
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [width, setWidth] = useState(opts.canvasWidth || 520)
  const [sessionKey, setSessionKey] = useState(opts.sessionKey || 'default')
  const [reloadKey, setReloadKey] = useState(0)
  // bumped when a run finishes — the one moment a balance can have changed
  const [runsCompleted, setRunsCompleted] = useState(0)
  // The main column shows ONE of these. Settings is a page rather than a modal for the same
  // reason it is in the shell: it is somewhere you go, and the conversation is one click back.
  const [view, setView] = useState<'chat' | 'settings'>('chat')
  // Collapsed sidebar, persisted like the shell's. `.sidebar--rail` is the shell's own
  // collapsed style, so this is the same 64px icon rail, not a second design.
  const [railed, setRailed] = useState(() => {
    try { return localStorage.getItem('agentd-rail') === '1' } catch { return false }
  })
  const toggleRail = () => setRailed((v) => {
    const next = !v
    try { localStorage.setItem('agentd-rail', next ? '1' : '0') } catch { /* private mode */ }
    return next
  })
  // The canvas editor's Send lands in whatever conversation is open — captured in a ref because
  // the canvas outlives any one render of the chat, and a stale sender would post into a thread
  // the user has already left.
  const sendRef = useRef<(text: string, atts: OutgoingAttachment[]) => Promise<void>>(async () => {})

  const host = useHost(
    { ...opts, sendToChat: (text, atts) => sendRef.current(text, atts as OutgoingAttachment[]) },
    setArtifact
  )

  // THE SCROLLBARS. The shell's thumb is transparent until a container is marked
  // `[data-scrolling]`, which this installer does app-wide (it also draws the edge fades). Without
  // it a scrollable pane looks like it does not scroll at all — no thumb, no fade, no affordance —
  // which is exactly how the first build of this window read.
  useEffect(() => installSoftScroll(), [])

  useEffect(() => {
    if (!artifact) return
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') setArtifact(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [artifact])

  function startResize(e: React.MouseEvent): void {
    e.preventDefault()
    const move = (ev: MouseEvent): void => setWidth(Math.max(320, window.innerWidth - ev.clientX))
    const up = (): void => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  return (
    <CanvasHostProvider value={host}>
      <div className="app agentd-canvas-host">
        {railed ? (
          <aside className="sidebar sidebar--rail">
            <button className="rail-btn" title="expand sidebar" onClick={toggleRail}><PanelLeft size={17} /></button>
            <button
              className="rail-primary"
              title="new chat"
              onClick={() => { setSessionKey(`chat-${Date.now().toString(36)}`); setReloadKey((n) => n + 1); setView('chat') }}
            >
              <SquarePen size={17} />
            </button>
            <div className="rail-sep" />
            <div className="rail-spacer" />
            <button className="rail-btn" title="Settings" onClick={() => setView((v) => (v === 'settings' ? 'chat' : 'settings'))}>
              <SettingsIcon size={17} />
            </button>
          </aside>
        ) : (
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-name">{opts.title}</span>
            <span className="live"><span className="live-dot" />live</span>
            <button className="icon-btn icon-btn--sm" title="collapse sidebar" onClick={toggleRail}>
              <PanelLeft size={17} />
            </button>
          </div>
          <SessionRail
            client={opts.client as ChatClient}
            agentId={opts.agentId}
            current={sessionKey}
            onPick={(key) => setSessionKey(key)}
            onNew={() => {
              // a fresh key IS a fresh conversation: the daemon creates one on first send, so
              // nothing has to be asked for up front
              setSessionKey(`chat-${Date.now().toString(36)}`)
              setReloadKey((n) => n + 1)
            }}
            reloadKey={reloadKey}
            onChanged={(deletedKey) => {
              setReloadKey((n) => n + 1)
              // Deleting the conversation you are IN must not leave the thread showing a
              // transcript the daemon no longer has — start a fresh one, as the shell does.
              if (deletedKey && deletedKey === sessionKey) setSessionKey(`chat-${Date.now().toString(36)}`)
            }}
          />
          <div className="app-files">
            <div className="section-label">Files</div>
            <WorkspaceTree agentId={opts.agentId || ''} />
          </div>
          <AccountFooter
            account={opts.account}
            runsCompleted={runsCompleted}
            onSettings={() => setView((v) => (v === 'settings' ? 'chat' : 'settings'))}
            settingsOpen={view === 'settings'}
          />
        </aside>
        )}

        <main className="main">
          {view === 'settings' && (
            <SettingsPage
              client={opts.client}
              account={opts.account}
              agentId={opts.agentId}
              title={opts.title}
              onClose={() => setView('chat')}
            />
          )}
          {/* MOUNTED, JUST HIDDEN. Unmounting the chat to show settings would drop the socket
              subscription and the scrollback mid-run — you would come back to a conversation that
              had visibly lost the answer it was streaming. */}
          <div className="app-pane" hidden={view !== 'chat'}>
          <ChatSurface
            client={opts.client as ChatClient}
            agentId={opts.agentId}
            sessionKey={sessionKey}
            title={opts.title}
            blurb={opts.blurb}
            suggestions={opts.suggestions}
            onReady={(api) => { sendRef.current = api.send }}
            onRunEnd={() => setRunsCompleted((n) => n + 1)}
          />
          </div>
        </main>

        {artifact && (
          <aside className="canvas" style={{ width }}>
            <div className="canvas-resize" onMouseDown={startResize} title="drag to resize" />
            <div className="canvas-head">
              <div className="canvas-title">
                <span className="canvas-name" title={artifact.path}>{artifact.name}</span>
                <span className="canvas-kind">{kindLabel(artifact)}</span>
              </div>
              <div className="canvas-head-actions">
                {artifact.text == null && (
                  <>
                    <a className="cv-btn" href={host.fileUrl(artifact.path)} target="_blank" rel="noreferrer" title="open in a new tab">
                      <ExternalLink size={15} />
                    </a>
                    <a className="cv-btn" href={host.fileUrl(artifact.path)} download={artifact.name} title="download a copy">
                      <Download size={15} />
                    </a>
                  </>
                )}
                <button className="cv-btn" title="close (Esc)" onClick={() => setArtifact(null)}><X size={16} /></button>
              </div>
            </div>
            <div className="canvas-body">
              {/* key by path so switching files fully remounts the viewer (fresh zoom/text) */}
              <CanvasBody key={artifact.path} a={artifact} />
            </div>
          </aside>
        )}
      </div>
    </CanvasHostProvider>
  )
}

export interface ShellOptions extends MountOptions {
  /** Product name — the rail header, the composer placeholder, the empty state. */
  title: string
  /** One line under the empty-state title. */
  blurb?: string
  /** Starter prompts shown on an empty conversation. */
  suggestions?: string[]
  /** Conversation to open first (default 'default'). */
  sessionKey?: string
  /** Opening width of the canvas panel, px. */
  canvasWidth?: number
  /**
   * WHO IS SIGNED IN and how to leave — rendered as the sidebar footer.
   *
   * Supplied by the app rather than built here, because reading a balance and revoking a session
   * both need a credential, and this bundle deliberately never touches one (the app owns the SDK,
   * therefore the one TokenManager). Omit it and the footer is simply absent, which is right for
   * a surface with no accounts behind it.
   */
  account?: AccountAdapter
}

/**
 * Mount the full agentd-style window into one element.
 *
 *   agentdCanvas.mountShell(document.getElementById('root'), {
 *     client: agentd.fromPage(), agentId: 'figure-creator', title: 'Figure Creator'
 *   })
 *
 * Load the shell stylesheet beside it (`vendor/agentd-shell.css`) — that is what makes this the
 * agentd UI rather than a page with the same element names.
 */
export function mountShell(el: HTMLElement, opts: ShellOptions): { unmount(): void } {
  const root = createRoot(el)
  root.render(<ShellApp opts={opts} />)
  return { unmount: () => root.unmount() }
}
