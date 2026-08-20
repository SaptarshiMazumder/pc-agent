/**
 * THE CHAT SURFACE, for an agent app — the shell's own conversation, over the SDK.
 *
 * Same rule as the canvas half of this bundle: nothing here is a lookalike. The scrollback is the
 * shell's `chat/Thread` rendering the shell's `MessageItem`; the transcript is built by the
 * shell's `chat/session` reducer from the same events; the class names are the shell's, so the
 * shell's stylesheet dresses it. What this file adds is only what an APP has to answer for
 * itself — which socket, which conversation, and a composer that talks to `client.send`.
 *
 * WHY THE COMPOSER IS NOT SHARED TOO. The shell's composer is welded to things an agent app has
 * no equivalent of: tabs, projects, @mentions of other installed agents, a credits chip that
 * navigates to a billing page. Sharing it would mean dragging the store behind it. So the markup
 * is mirrored — same elements, same classes, same keyboard behaviour — and the parts that only
 * make sense in the shell are simply absent. If the composer ever loses those attachments, it
 * should move into ui/src/chat and this copy should go.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent, type JSX } from 'react'
import { ArrowUp, MessageSquare, Paperclip, Plus, Square, Upload, X } from 'lucide-react'

import { ChatHostProvider, type ChatHost } from '../../ui/src/chat/host'
import Thread from '../../ui/src/chat/Thread'
import {
  emptySession,
  historyToItems,
  reduceEvent,
  type SessionState
} from '../../ui/src/chat/session'
import FileName from '../../ui/src/components/FileName'
import SessionItem from '../../ui/src/components/SessionItem'
import { SessionsHostProvider, type SessionsHost } from '../../ui/src/chat/sessionsHost'

/** What a conversation needs of a client — a subset of @agentd/client, named so this file can be
 *  read (and tested) without the SDK's whole surface. */
export interface ChatClient {
  request<T = Record<string, unknown>>(method: string, params?: Record<string, unknown>): Promise<T>
  fileUrl(path: string): string
  onStatus(handler: (s: string) => void): () => void
  onRun(sessionKey: string, handler: (payload: any) => void): () => void
  send(opts: {
    message: string
    sessionKey?: string
    agentId?: string
    attachments?: { name: string; mimeType: string; dataBase64: string }[]
  }): Promise<unknown>
  abort(sessionKey: string): Promise<unknown>
  sessions(agentId?: string): Promise<{ sessions: any[] }>
  history(sessionKey: string, agentId?: string): Promise<{ messages: any[] }>
}

export interface OutgoingAttachment {
  name: string
  mimeType: string
  dataBase64: string
}

const MAX_ATTACHMENTS = 8

function attachmentName(f: File): string {
  return f.name || `pasted-${Date.now()}`
}

/** A picked file as the wire shape: bytes ride the send as base64 and the daemon writes them into
 *  the workspace, after which they render like any other artifact. */
function fileToAttachment(f: File): Promise<OutgoingAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error)
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve({
        name: attachmentName(f),
        mimeType: f.type || 'application/octet-stream',
        dataBase64: result.slice(result.indexOf(',') + 1)
      })
    }
    reader.readAsDataURL(f)
  })
}

/**
 * A counter that increments every time the socket OPENS — the dependency every read must carry.
 *
 * `client.request` REJECTS with "not connected" until the WebSocket is open, and a React effect
 * runs on mount, which is earlier. So the first load of the conversation list and of a
 * transcript both failed, silently, and the sidebar sat on "no conversations yet" until something
 * else happened to re-run the effect — which is why the chats appeared only after pressing New
 * chat. Keyed on a COUNTER rather than a boolean so a reconnect re-reads too: coming back from a
 * dropped socket should show what changed while you were away.
 */
function useConnected(client: { onStatus(h: (s: string) => void): () => void }): number {
  const [opens, setOpens] = useState(0)
  useEffect(() => client.onStatus((s) => { if (s === 'open') setOpens((n) => n + 1) }), [client])
  return opens
}

/**
 * ONE conversation: its transcript, whether a run is live, and how to add to it.
 *
 * The daemon broadcasts every session's events to every authorised socket, so the filtering is
 * `client.onRun(sessionKey, …)`. History is re-read whenever the conversation changes, which is
 * also what makes a reload land exactly where the user left off.
 */
export function useConversation(client: ChatClient, sessionKey: string, agentId?: string) {
  const [session, setSession] = useState<SessionState>(emptySession)
  const [seed, setSeed] = useState('')
  const opened = useConnected(client)

  useEffect(() => {
    if (!opened) return // `request` REJECTS before the socket is open — see useConnected
    let alive = true
    setSession(emptySession())
    void client
      .history(sessionKey, agentId)
      .then((r) => {
        if (alive) setSession({ items: historyToItems(r.messages || []), running: false })
      })
      .catch(() => {
        /* a conversation with no transcript yet — an empty thread is the right render */
      })
    const off = client.onRun(sessionKey, (payload) => {
      // the server stamps every live event (epoch seconds) so all clients agree on time
      const ts = typeof payload?.ts === 'number' ? payload.ts * 1000 : Date.now()
      setSession((s) => reduceEvent(s, (payload?.event || {}) as any, ts))
    })
    return () => {
      alive = false
      off()
    }
  }, [client, sessionKey, agentId, opened])

  const send = useCallback(
    async (text: string, attachments: OutgoingAttachment[] = []) => {
      const message = text.trim()
      if (!message && !attachments.length) return
      const ts = Date.now()
      // Optimistic: the user's own line appears immediately, exactly as in the shell — the run
      // only starts once the daemon has it, and a send that fails leaves a visible message plus
      // the error rather than a composer that silently emptied itself.
      setSession((s) => ({
        ...s,
        running: true,
        items: [
          ...s.items,
          { kind: 'user', text: message, ts, ...(attachments.length ? { artifacts: [] } : {}) }
        ]
      }))
      try {
        await client.send({
          message,
          sessionKey,
          ...(agentId ? { agentId } : {}),
          ...(attachments.length ? { attachments } : {})
        })
      } catch (e) {
        setSession((s) => ({
          ...s,
          running: false,
          items: [...s.items, { kind: 'system', tone: 'error', text: String((e as Error)?.message || e), ts: Date.now() }]
        }))
      }
    },
    [client, sessionKey, agentId]
  )

  const abort = useCallback(async () => {
    try {
      await client.abort(sessionKey)
    } catch {
      /* nothing running, or a daemon that already forgot it — the run state settles either way */
    }
  }, [client, sessionKey])

  return { session, send, abort, seed, setSeed }
}

/** The composer — the shell's markup, minus what only a shell can offer. */
function Composer({
  connected,
  running,
  placeholder,
  hint,
  seed,
  onSend,
  onAbort
}: {
  connected: boolean
  running: boolean
  placeholder: string
  hint: string
  seed: string
  onSend(text: string, attachments: OutgoingAttachment[]): Promise<void>
  onAbort(): Promise<void>
}): JSX.Element {
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<OutgoingAttachment[]>([])
  const [menuOpen, setMenuOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!seed) return
    setDraft(seed)
    taRef.current?.focus()
  }, [seed])

  // grow with the text, like the shell's
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`
  }, [draft])

  async function pickFiles(list: FileList | File[] | null): Promise<void> {
    const files = Array.from(list || []).slice(0, MAX_ATTACHMENTS - pending.length)
    if (!files.length) return
    const next = await Promise.all(files.map(fileToAttachment))
    setPending((p) => [...p, ...next].slice(0, MAX_ATTACHMENTS))
  }

  function submit(e?: FormEvent): void {
    e?.preventDefault()
    if (!draft.trim() && !pending.length) return
    const text = draft
    const atts = pending
    setDraft('')
    setPending([])
    void onSend(text, atts)
  }

  return (
    <form className="composer" onSubmit={submit}>
      {pending.length > 0 && (
        <div className="composer-atts">
          {pending.map((a, i) => (
            <span className={`att-chip ${a.mimeType.startsWith('image/') ? 'att-chip--img' : ''}`} key={`${a.name}-${i}`} title={a.name}>
              {a.mimeType.startsWith('image/') ? (
                <img className="att-thumb" src={`data:${a.mimeType};base64,${a.dataBase64}`} alt="" />
              ) : (
                <Paperclip size={12} />
              )}
              <FileName name={a.name} className="att-name" />
              <button type="button" className="att-remove" title="remove" onClick={() => setPending((p) => p.filter((_, j) => j !== i))}>
                <X size={12} />
              </button>
            </span>
          ))}
          {pending.length >= MAX_ATTACHMENTS && <span className="att-limit">Max {MAX_ATTACHMENTS} files</span>}
        </div>
      )}
      <div className="composer-box">
        <div className="composer-attach-wrap">
          <button type="button" className={`composer-attach ${menuOpen ? 'active' : ''}`} title="add" onClick={() => { setMenuOpen(false); fileRef.current?.click() }}>
            <Plus size={19} />
          </button>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { void pickFiles(e.target.files); e.target.value = '' }} />
        </div>
        <textarea
          ref={taRef}
          value={draft}
          placeholder={connected ? placeholder : 'connecting…'}
          disabled={!connected}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData?.files || [])
            if (files.length) { e.preventDefault(); void pickFiles(files) }
          }}
          rows={1}
        />
        {running ? (
          <button type="button" className="composer-send stop" onClick={() => void onAbort()} title="stop the run">
            <Square size={13} fill="currentColor" strokeWidth={0} />
          </button>
        ) : (
          <button type="submit" className={`composer-send ${draft.trim() || pending.length ? 'ready' : ''}`} disabled={(!draft.trim() && !pending.length) || !connected} title="send">
            <ArrowUp size={18} />
          </button>
        )}
      </div>
      <div className="composer-hint">
        <span className="hint-model">{hint || 'agentd'}</span>
        <span className="hint-sep"> · </span>
        <span className="hint-note">{running ? 'running — press Stop to interrupt' : 'ready'}</span>
      </div>
    </form>
  )
}

export interface ChatSurfaceOptions {
  client: ChatClient
  agentId?: string
  sessionKey: string
  /** Product name — the placeholder and the empty state say it. */
  title: string
  /** One line under the empty-state title. */
  blurb?: string
  /** Starter prompts on an empty conversation. */
  suggestions?: string[]
  /** Somewhere for the tool gear to go. An app usually has nowhere, so it is optional. */
  openToolConfig?(toolName: string): void
  /** Handed back so the surrounding app can wire the canvas's send-to-chat into this thread. */
  onReady?(api: { send(text: string, attachments: OutgoingAttachment[]): Promise<void> }): void
  /** A run finished (well or badly). The account footer re-reads the balance on this rather than
   *  polling: it is the only moment the number can have moved. */
  onRunEnd?(): void
}

/**
 * The whole conversation pane: scrollback, empty-state hero, composer.
 *
 * Provides the ChatHost the shared message components read, so it must sit ABOVE them — and it is
 * rendered inside the caller's CanvasHostProvider, which is what makes an artifact in a message
 * open in the app's canvas on click.
 */
export function ChatSurface(opts: ChatSurfaceOptions): JSX.Element {
  const { session, send, abort, seed, setSeed } = useConversation(opts.client, opts.sessionKey, opts.agentId)
  const [connected, setConnected] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => opts.client.onStatus((s) => setConnected(s === 'open')), [opts.client])
  useEffect(() => { opts.onReady?.({ send }) }, [send, opts])

  // edge-triggered: running -> not running is a finished run. Reading it off the conversation
  // rather than the event stream means a run someone else started (a second window on the same
  // session) settles the footer too.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !session.running) opts.onRunEnd?.()
    wasRunning.current = session.running
  }, [session.running, opts])

  // pin to the bottom as the answer streams, the way the shell does
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [session.items])

  const host: ChatHost = {
    running: session.running,
    seedComposer: (text: string) => setSeed(text),
    openToolConfig: (tool: string) => opts.openToolConfig?.(tool)
  }

  const empty = session.items.length === 0
  const usage = session.usage
  const hint = usage?.model
    ? `${usage.model}  ·  ${usage.tokensIn.toLocaleString()} in / ${usage.tokensOut.toLocaleString()} out`
    : opts.title

  const composer = (
    <Composer
      connected={connected}
      running={session.running}
      placeholder={`Message ${opts.title}…`}
      hint={hint}
      seed={seed}
      onSend={send}
      onAbort={abort}
    />
  )

  // DROP FILES ANYWHERE ON THE CONVERSATION — the shell does, and a figure agent is exactly the
  // case for it: you drag in a reference image rather than hunting for the paperclip. The counter
  // (not a boolean) is what survives dragenter/dragleave firing for every child element crossed.
  const [dragDepth, setDragDepth] = useState(0)
  const dragging = dragDepth > 0

  function hasFiles(dt: DataTransfer | null): boolean {
    return !!dt && Array.from(dt.types || []).includes('Files')
  }

  async function dropFiles(list: FileList | null): Promise<void> {
    const files = Array.from(list || [])
    if (!files.length) return
    const atts = await Promise.all(files.slice(0, MAX_ATTACHMENTS).map(fileToAttachment))
    // Straight into the conversation with no text: the shell stages them in the composer, but a
    // window whose composer may be one pane away should not silently park them out of sight.
    await send('', atts)
  }

  return (
    <ChatHostProvider value={host}>
      <div
        className={`chat ${empty ? 'empty' : ''}`}
        onDragEnter={(e) => { if (hasFiles(e.dataTransfer)) { e.preventDefault(); setDragDepth((d) => d + 1) } }}
        onDragOver={(e) => { if (hasFiles(e.dataTransfer)) e.preventDefault() }}
        onDragLeave={() => setDragDepth((d) => Math.max(0, d - 1))}
        onDrop={(e) => {
          if (!hasFiles(e.dataTransfer)) return
          e.preventDefault()
          setDragDepth(0)
          void dropFiles(e.dataTransfer.files)
        }}
      >
        {dragging && (
          <div className="chat-dropzone" aria-hidden>
            <div className="chat-dropzone-inner">
              <Upload size={28} />
              <span>Drop files to attach</span>
            </div>
          </div>
        )}
        {empty ? (
          <div className="chat-hero">
            <div className="empty-state">
              <div className="empty-title">{opts.title}</div>
              <div className="empty-sub">{opts.blurb || 'Ask for anything — it runs here.'}</div>
            </div>
            <div className="chat-hero-composer">{composer}</div>
            {!!opts.suggestions?.length && (
              <div className="suggestions">
                {opts.suggestions.slice(0, 3).map((text) => (
                  <button key={text} className="suggestion" onClick={() => setSeed(text)}>
                    <MessageSquare size={15} />
                    {text}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="chat-scroll" ref={scrollRef}>
              <Thread items={session.items} />
            </div>
            {composer}
          </>
        )}
      </div>
    </ChatHostProvider>
  )
}

export interface SessionRow {
  /** The conversation's key on the wire — `sessions.list` calls it `sessionId`, and it is what
   *  `sessions.history` and `chat.send` both take as `sessionKey`. Named as the protocol names
   *  it (clients/sdk-js/src/protocol.ts): guessing `id`/`key` here produced an empty string, a
   *  history call for a conversation that does not exist, and a chat that opened blank. */
  sessionId: string
  title?: string
  snippet?: string
  messages?: number
  modified?: number
}

/**
 * The conversation list — THE SHELL'S OWN ROW, on the app-tier `sessions.*` methods.
 *
 * This used to render a hand-made row with three inline icons, and it was the one part of the
 * window that visibly was not the product: the shell's row has a ⋯ menu with five items, a
 * two-step delete, double-click-to-rename, a hover tooltip and an agent dot. Reproducing that
 * faithfully is not a styling job, so the row is no longer reproduced — `SessionItem` and
 * `ChatMenu` are imported from the shell and fed through the SessionsHost seam.
 *
 * `projects` is `[]` here on purpose: projects are host-only, and ChatMenu hides "Move to
 * project" when there is nowhere to move to. The app does not branch on being an app.
 */
export function SessionRail({
  client,
  agentId,
  current,
  onPick,
  onNew,
  reloadKey,
  onChanged
}: {
  client: ChatClient
  agentId?: string
  current: string
  onPick(key: string): void
  onNew(): void
  reloadKey: number
  onChanged(deletedKey?: string): void
}): JSX.Element {
  const [rows, setRows] = useState<SessionRow[]>([])
  const [query, setQuery] = useState('')
  const [agents, setAgents] = useState<any[]>([])
  const opened = useConnected(client)

  useEffect(() => {
    if (!opened) return // nothing can be read before the socket opens — see useConnected
    let alive = true
    void client
      .sessions(agentId)
      .then((r) => { if (alive) setRows(r.sessions || []) })
      .catch(() => { if (alive) setRows([]) })
    void client
      .request<{ agents: any[] }>('agents.list')
      .then((r) => { if (alive) setAgents(r.agents || []) })
      .catch(() => { /* the row falls back to the agent id */ })
    return () => { alive = false }
  }, [client, agentId, reloadKey, opened])

  const host: SessionsHost = {
    renameSession: async (id, title) => {
      await client.request('sessions.rename', { sessionKey: id, title })
      onChanged()
    },
    deleteSession: async (id) => {
      await client.request('sessions.delete', { sessionKey: id })
      onChanged(id)
    },
    duplicateSession: async (id) => {
      await client.request('sessions.duplicate', { sessionKey: id })
      onChanged()
    },
    // Never reached: `projects` below is empty, so the menu item that calls this is not rendered.
    moveSession: async () => {},
    exportSessionMd: async (id) => {
      // The transcript this window can already read, written out client-side — no host call, and
      // no second definition of what a chat looks like as Markdown than the one the daemon holds.
      const { messages } = await client.history(id, agentId)
      const text = historyToItems(messages || [])
        .map((item) => {
          if (item.kind === 'user') return `## You\n\n${item.text}`
          if (item.kind === 'assistant') return `## Assistant\n\n${item.text}`
          if (item.kind === 'tool') return `### tool: ${item.name}\n\n${item.result}`
          return ''
        })
        .filter(Boolean)
        .join('\n\n')
      const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `${id}.md`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 4000)
    },
    projects: [],
    agents
  }

  const q = query.trim().toLowerCase()
  const shown = rows
    .filter((row) => row.sessionId)
    .filter((row) => !q || (row.title || row.sessionId).toLowerCase().includes(q))

  return (
    <SessionsHostProvider value={host}>
      <div className="sidebar-scroll">
        <button className="row" onClick={onNew} title="Start a new conversation">
          <Plus size={16} />
          <span className="row-main"><span className="row-title">New chat</span></span>
        </button>
        {rows.length > 6 && (
          <input
            className="input side-search"
            placeholder="Search chats"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
        <div className="section-label">Chats</div>
        {shown.map((row) => (
          <SessionItem
            key={row.sessionId}
            session={row as any}
            active={row.sessionId === current}
            onOpen={() => onPick(row.sessionId)}
          />
        ))}
        {!shown.length && <div className="row-sub list-empty">{q ? 'no matching chats' : 'no conversations yet'}</div>}
      </div>
    </SessionsHostProvider>
  )
}

export { Upload }
