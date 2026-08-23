/* The daemon, as React hooks. THIS FILE IS THE PART WORTH COPYING.
 *
 * Every agent app is a client of the same daemon over one WebSocket, and the same four
 * mistakes get made when it is written from scratch:
 *
 *   1. the run-event payload is NESTED — `payload.event.type`, not `payload.type`
 *   2. streamed text is `message_update` / `text_delta`; `message_delta` does not exist
 *   3. the socket outlives React, so effects must unsubscribe or handlers stack up per render
 *   4. text and tool calls get stored in SEPARATE fields, which silently discards their order
 *
 * All four are invisible at runtime: the socket connects, the console stays clean, and the
 * screen is either empty or subtly wrong. Hooks are the fix — subscribe once, clean up on
 * unmount, and the wrong event name becomes a compile error rather than silence.
 */

import {
  authStatus,
  authLogout,
  fromPage,
  resultText,
  setRunMode,
  type AgentdClient,
  type Attachment,
  type AuthState,
  type RunMode,
} from '@agentd/client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

export type Status = 'connecting' | 'open' | 'closed'

/** One client for the life of the page. `fromPage()` reads the token and `scope=agent:<id>`
 *  the opener put in the URL — which is why NO agent id is ever written into this app: the
 *  daemon forces this agent onto every request it makes. */
export function useClient(): { client: AgentdClient; status: Status } {
  const client = useMemo(() => fromPage(), [])
  const [status, setStatus] = useState<Status>('connecting')
  useEffect(() => client.onStatus((s) => setStatus(s as Status)), [client])
  return { client, status }
}

// ---------------------------------------------------------------------------
// A turn is an ORDERED LIST OF BLOCKS.
// ---------------------------------------------------------------------------
// The obvious model — `{ text: string, tools: ToolRow[] }` — is wrong, and wrong in a way that
// looks fine until an agent does real work. A turn that reasons, calls a tool, comments on the
// result, calls another, then answers has FIVE things in a specific order; two parallel fields
// can only render "all the tools, then all the prose". The user sees a wall of tool names with
// four unrelated sentences fused into one paragraph underneath, and no way to tell which
// sentence belongs to which call.
//
// Keeping one ordered array is barely more code and is the entire difference between a
// transcript and a log dump.

export interface TextBlock {
  kind: 'text'
  text: string
}

/** The model's reasoning. Rendered dimmed and collapsible — it is context, not the answer, but
 *  WITHOUT IT a long research phase is a column of tool names and a spinner, which is the most
 *  common reason a working agent feels hung. */
export interface ThinkingBlock {
  kind: 'thinking'
  text: string
}

/** Run `fn` once the socket is open — immediately if it already is, and again after every
 *  reconnect.
 *
 *  EVERY LOAD-ON-MOUNT NEEDS THIS, and forgetting it is the most confusing bug in this file.
 *  `client.request` REJECTS synchronously with "not connected" while the socket is still
 *  opening, and a React effect runs before the handshake finishes. So the first render of every
 *  panel fires a request that cannot succeed, and nothing retries — from React's point of view
 *  the load already happened. What the user sees is a settings page saying "not connected" next
 *  to a green connection dot, an empty history list, and no way to tell any of it from a real
 *  permission failure.
 *
 *  Reconnect matters as much as first connect: after the daemon restarts, a panel that loaded
 *  once keeps showing what was true before the restart.
 */
export function useWhenOpen(client: AgentdClient, fn: () => void) {
  useEffect(() => {
    if (client.connected) fn()
    let wasOpen = client.connected
    return client.onStatus((s) => {
      const open = s === 'open'
      if (open && !wasOpen) fn()
      wasOpen = open
    })
  }, [client, fn])
}

export interface ToolBlock {
  kind: 'tool'
  id: string
  name: string
  done: boolean
  ok: boolean
  detail: string
  /** The tool's own running commentary (`tool_progress`) — retries, page fetches, per-step
   *  notes. A slow tool with no progress line is indistinguishable from a hung one. */
  progress: string
}

/** Something the RUNTIME says about the run, not something the model said: the provider error
 *  that ended it, or a model swap. It is not part of the conversation, so it does not look like
 *  one — but it is the only place the reason for a failure ever appears. */
export interface NoteBlock {
  kind: 'note'
  tone: 'error' | 'warn'
  text: string
}

export type Block = TextBlock | ThinkingBlock | ToolBlock | NoteBlock

/** An image on screen. `src` is a data URI for something just attached and a `/file` URL for
 *  something restored from the transcript — the transcript stores uploads BY REFERENCE, so the
 *  bytes are on the daemon, not in the history payload. */
export interface ThreadImage {
  name: string
  src: string
}

export interface Turn {
  role: 'user' | 'assistant'
  blocks: Block[]
  streaming: boolean
  images: ThreadImage[]
}

/** Where this window's current conversation id lives across reloads.
 *
 *  A fresh random key per mount means every reload silently starts a new chat and abandons the
 *  last one — the conversation is still on disk, but nothing points at it, so to the user the
 *  agent simply forgot. Keyed by agent so two agent windows do not share one thread.
 */
const SESSION_STORE = 'agentd:session'

function storedSession(agentId: string): string {
  const key = `${SESSION_STORE}:${agentId}`
  try {
    const found = localStorage.getItem(key)
    if (found) return found
    const made = `chat-${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(key, made)
    return made
  } catch {
    // Private browsing, or storage disabled. Degrade to a per-mount key: the chat still works,
    // it just will not survive a reload.
    return `chat-${Math.random().toString(36).slice(2, 10)}`
  }
}

/** The agent this window is scoped to. `?scope=agent:<id>` — the daemon strips the prefix before
 *  forcing the agent onto our requests, so anything keyed BY agent has to strip it too. */
export function pageAgentId(): string {
  return (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '') || 'agent'
}

/** One stored message -> a Turn. The shape is the TRANSCRIPT's, not the event stream's: content
 *  is an array of blocks, and tool calls live in `tool_use` blocks rather than in separate
 *  events. That array is ALREADY in order, so restoring it in order is what makes a reloaded
 *  chat look like the one you left rather than a regrouped summary of it. */
function turnFromMessage(m: any, fileUrl: (path: string) => string): Turn | null {
  const raw: any[] = Array.isArray(m?.content)
    ? m.content
    : typeof m?.content === 'string'
      ? [{ type: 'text', text: m.content }]
      : []

  const blocks: Block[] = []
  for (const [i, c] of raw.entries()) {
    if (c?.type === 'text' && String(c.text || '').trim()) {
      blocks.push({ kind: 'text', text: String(c.text) })
    } else if (c?.type === 'thinking' && String(c.thinking || c.text || '').trim()) {
      blocks.push({ kind: 'thinking', text: String(c.thinking || c.text) })
    } else if (c?.type === 'tool_use' || c?.type === 'toolcall') {
      blocks.push({
        kind: 'tool',
        id: String(c.id ?? `${i}`),
        name: String(c.name ?? 'tool'),
        done: true,
        // The transcript does not record whether a past call failed, so it is shown as completed
        // rather than as a green tick it has not earned.
        ok: true,
        detail: '',
        progress: '',
      })
    }
  }

  // Uploads are stored as artifacts alongside the message, not as content blocks.
  const images: ThreadImage[] = (m?.attachments ?? [])
    .filter((a: any) => a?.kind === 'image' && a?.path)
    .map((a: any) => ({ name: String(a.name || 'image'), src: fileUrl(String(a.path)) }))

  if (!blocks.length && !images.length) return null
  return {
    role: m?.role === 'user' ? 'user' : 'assistant',
    blocks,
    streaming: false,
    images,
  }
}

/** A conversation: history, streaming text and reasoning, and the tool rows — all in one
 *  ordered list per turn.
 *
 *  `onToolDone` is the hook that keeps the rest of the screen honest — when the agent finishes
 *  a tool IN CHAT, whatever that tool changed should refresh. Without it a panel shows the state
 *  from before the agent acted, and the user is looking at a lie with no way to know. */
export function useChat(client: AgentdClient, opts: { onToolDone?: (name: string) => void } = {}) {
  const agentId = useMemo(() => pageAgentId(), [])
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [sessionKey, setSessionKey] = useState(() => storedSession(agentId))
  const sessionRef = useRef<string>(sessionKey)
  sessionRef.current = sessionKey
  const onToolDone = useRef(opts.onToolDone)
  onToolDone.current = opts.onToolDone

  // RESTORE. Without this a reload shows an empty thread over a conversation that is still on
  // disk and still being appended to — the next reply arrives with no visible question.
  const restore = useCallback(() => {
    void (async () => {
      try {
        const res = await client.history(sessionRef.current)
        const restored = (res?.messages ?? [])
          .map((m: any) => turnFromMessage(m, (p) => client.fileUrl(p)))
          .filter(Boolean) as Turn[]
        if (restored.length) setTurns(restored)
      } catch {
        // A key with no transcript yet is the normal first-run state, not an error worth showing.
      }
    })()
    // sessionKey is the real dependency — sessionRef mirrors it and is read for the CURRENT
    // value at call time, which is what a reconnect needs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, sessionKey])

  useWhenOpen(client, restore)

  useEffect(() => {
    // ONE subscription for the page. The returned unsubscribe is the whole reason this lives in
    // an effect: without it every re-render adds another handler and each delta is appended
    // twice, then four times, then eight.
    return client.on('chat.event', (payload: any) => {
      if (payload?.sessionKey !== sessionRef.current) return
      const event = payload?.event
      if (!event) return

      // THE NESTING. `payload.event.type` — reading `payload.type` here is the single most
      // common way a generated UI ends up connected, silent and empty.
      switch (event.type) {
        case 'message_update': {
          const delta = String(event.delta ?? '') // `delta`, verified — not `text`
          if (!delta) return
          // Reasoning and answer arrive on the SAME event under different kinds. Both are
          // appended in arrival order, so a thought that preceded a tool call stays before it.
          if (event.kind === 'thinking_delta') {
            setTurns((prev) => withCurrentAssistant(prev, (t) => appendDelta(t, 'thinking', delta)))
            return
          }
          if (event.kind !== 'text_delta') return // NOT 'message_delta' — no such event
          setTurns((prev) => withCurrentAssistant(prev, (t) => appendDelta(t, 'text', delta)))
          return
        }
        case 'tool_execution_start': {
          const row: ToolBlock = {
            kind: 'tool',
            id: String(event.toolCallId ?? event.toolName ?? Math.random()),
            name: String(event.toolName ?? 'tool'),
            done: false,
            ok: true,
            detail: '',
            progress: '',
          }
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) => ({ ...t, blocks: [...t.blocks, row] })),
          )
          return
        }
        case 'tool_progress': {
          const text = String(event.text ?? '').trim()
          if (!text) return
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) =>
              updateTool(t, (b) => !b.done, (b) => ({ ...b, progress: text })),
            ),
          )
          return
        }
        case 'tool_execution_end': {
          const id = String(event.toolCallId ?? '')
          const name = String(event.toolName ?? '')
          const ok = !event.isError
          const detail = String(event.summary ?? '')
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) =>
              updateTool(
                t,
                (b) => b.id === id || (!id && b.name === name && !b.done),
                (b) => ({ ...b, done: true, ok, detail }),
              ),
            ),
          )
          // Tell the rest of the app something changed. Named, so a panel can ignore tools it
          // does not care about.
          if (name) onToolDone.current?.(name)
          return
        }
        case 'model_fallback': {
          // The configured model could not serve the request and another one took over. Silence
          // here means the user judges a substitute's output as if it came from what they chose.
          const from = String(event.from ?? 'the configured model')
          const to = String(event.to ?? 'a fallback model')
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) => ({
              ...t,
              blocks: [
                ...t.blocks,
                { kind: 'note', tone: 'warn', text: `${from} could not answer — ${to} took over.` },
              ],
            })),
          )
          return
        }
        case 'agent_end': {
          setBusy(false)
          // THE REASON THE RUN FAILED ARRIVES HERE, and only here.
          //
          // When the model errors, the transcript gets a flat "Agent couldn't generate a
          // response." while the provider's actual words — dead key, rate limit, context
          // overflow, refused request — ride on `agent_end.error`. Dropping it, as this used to,
          // turns every distinct failure into the same shrug and leaves the user re-sending a
          // message that will fail identically.
          const failure = String(event.error ?? '').trim()
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) => ({
              ...t,
              streaming: false,
              blocks: failure
                ? [...t.blocks, { kind: 'note', tone: 'error', text: failure }]
                : t.blocks,
            })),
          )
          return
        }
      }
    })
  }, [client])

  const ask = useCallback(
    async (text: string, attachments: Attachment[] = []) => {
      const message = text.trim()
      // A message may be attachments-ONLY — "what is wrong with this render?" over a screenshot
      // is a complete request, and the daemon accepts it, so requiring text here would be this
      // app inventing a restriction the runtime does not have.
      if ((!message && !attachments.length) || busy) return
      setTurns((prev) => [
        ...prev,
        {
          role: 'user',
          blocks: message ? [{ kind: 'text', text: message }] : [],
          streaming: false,
          // Shown from the bytes we already hold; on the next reload the same images come back
          // from the daemon by path.
          images: attachments.map((a) => ({
            name: a.name,
            src: `data:${a.mimeType || 'application/octet-stream'};base64,${a.dataBase64}`,
          })),
        },
      ])
      setBusy(true)
      try {
        // No agentId — the daemon scopes this connection to our own agent already.
        await client.send({
          message,
          sessionKey: sessionRef.current,
          ...(attachments.length ? { attachments } : {}),
        })
      } catch (e) {
        setBusy(false)
        setTurns((prev) => [
          ...prev,
          {
            role: 'assistant',
            blocks: [{ kind: 'text', text: `could not send: ${String(e)}` }],
            streaming: false,
            images: [],
          },
        ])
      }
    },
    [client, busy],
  )

  /** Interrupt the run. THE COMPOSER IS NOT A DEAD BOX WHILE THE AGENT WORKS.
   *
   *  A long research turn can run for minutes, and the one thing a person most wants to do
   *  during it is say "stop, that is not what I meant". Disabling the only control on screen
   *  makes them watch the mistake finish. */
  const stop = useCallback(async () => {
    try {
      await client.abort(sessionRef.current)
    } catch (e) {
      // A refused abort must SAY so — silence here reads as "stop did nothing", and the user
      // presses it again while the run carries on.
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          blocks: [{ kind: 'text', text: `could not stop: ${String(e)}` }],
          streaming: false,
          images: [],
        },
      ])
    }
  }, [client])

  const remember = useCallback(
    (key: string) => {
      try {
        localStorage.setItem(`${SESSION_STORE}:${agentId}`, key)
      } catch {
        // storage disabled — the switch still works for this session
      }
      setSessionKey(key)
      setTurns([])
      setBusy(false)
    },
    [agentId],
  )

  const reset = useCallback(
    () => remember(`chat-${Math.random().toString(36).slice(2, 10)}`),
    [remember],
  )

  /** Switch to an existing conversation. The effect above reloads its transcript. */
  const open = useCallback((key: string) => remember(key), [remember])

  return { turns, busy, ask, stop, reset, open, sessionKey }
}

/** Append streamed text to the last block IF it is still the same kind, otherwise start a new
 *  one. This one rule is what interleaves prose and tools correctly: a tool call between two
 *  sentences ends the first block, so the second sentence lands after the tool instead of being
 *  glued onto text written before it. */
function appendDelta(turn: Turn, kind: 'text' | 'thinking', delta: string): Turn {
  const blocks = [...turn.blocks]
  const last = blocks[blocks.length - 1]
  if (last && last.kind === kind) {
    blocks[blocks.length - 1] = { ...last, text: last.text + delta }
  } else {
    blocks.push({ kind, text: delta })
  }
  return { ...turn, blocks }
}

/** Update the LAST tool block matching `match` — last, because a tool called twice in one turn
 *  produces two blocks with the same name, and the running one is always the later. */
function updateTool(
  turn: Turn,
  match: (b: ToolBlock) => boolean,
  fn: (b: ToolBlock) => ToolBlock,
): Turn {
  const blocks = [...turn.blocks]
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i]
    if (b.kind === 'tool' && match(b)) {
      blocks[i] = fn(b)
      return { ...turn, blocks }
    }
  }
  return turn
}

function withCurrentAssistant(turns: Turn[], fn: (t: Turn) => Turn): Turn[] {
  const next = [...turns]
  const last = next[next.length - 1]
  if (last?.role === 'assistant' && last.streaming) {
    next[next.length - 1] = fn(last)
  } else {
    next.push(fn({ role: 'assistant', blocks: [], streaming: true, images: [] }))
  }
  return next
}

/** Files (dropped, pasted or picked) -> the wire shape the daemon takes.
 *
 *  A pasted screenshot usually has NO usable filename, and the daemon stores it as literally
 *  "attachment" — no extension, so it is not classified as an image, so a vision model never
 *  receives it as one. Naming it from the mime type is what makes paste work at all.
 */
export async function readAttachments(
  files: FileList | File[],
  max = 10,
): Promise<Attachment[]> {
  const out: Attachment[] = []
  for (const file of Array.from(files).slice(0, max)) {
    const dataBase64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(file)
    })
    if (!dataBase64) continue
    out.push({
      name: attachmentName(file),
      mimeType: file.type || 'application/octet-stream',
      dataBase64,
    })
  }
  return out
}

function attachmentName(file: File): string {
  if (file.name && file.name.includes('.')) return file.name
  const ext = (file.type.split('/')[1] || 'bin').split('+')[0].replace(/[^a-z0-9]/gi, '')
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  return `${file.name || 'pasted'}-${stamp}.${ext}`
}

/** Call one of THIS agent's tools directly — no chat turn, no model call, no tokens.
 *
 *  This is what makes an app more than a transcript: a button that does the thing, in
 *  milliseconds. Use chat for what needs judgement and this for what needs doing. */
export function useTool(client: AgentdClient) {
  return useCallback(
    async (name: string, params: Record<string, unknown> = {}): Promise<string> => {
      const res = await client.invokeTool(name, params)
      return resultText(res)
    },
    [client],
  )
}

/** Files in the agent's workspace, straight from the daemon. One concern. */
export function useWorkspace(client: AgentdClient) {
  return useCallback(
    async (path = ''): Promise<Array<{ name: string; path: string; size: number }>> => {
      const res: any = await client.request('workspace.list', path ? { path } : {})
      return (res?.entries ?? res?.files ?? []) as Array<{ name: string; path: string; size: number }>
    },
    [client],
  )
}

export interface WorkflowEntry {
  name: string
  path: string
  format: 'api' | 'ui' | ''
  nodes: number
  runnable: boolean
}

/** The workflows, ASKED OF THE AGENT'S OWN TOOL and read as data.
 *
 *  TWO LESSONS HERE, both learned the hard way.
 *
 *  1. NOT `workspace.list`. The daemon resolves a workspace per caller — a signed-in window and
 *     an agent run can legitimately get different roots — so the gateway happily reported an
 *     empty folder while the file existed. The tool uses `current_workspace`, which is the same
 *     path the agent was told to write to, so asking the tool asks the right question.
 *
 *  2. READ `details`, NEVER THE TEXT. A tool's prose is written for the model; parsing it in a
 *     UI is scraping. It was done here with a regex over "3 workflow(s) in …" for exactly one
 *     afternoon, and a reworded line made this panel render "Nothing built yet" over a folder
 *     with two files in it — no error, nothing in the console, nothing to search for.
 */
export function useWorkflows(client: AgentdClient) {
  return useCallback(async (): Promise<WorkflowEntry[]> => {
    const res: any = await client.invokeTool('list_workflows', {})
    const found = res?.details?.workflows
    if (!Array.isArray(found)) {
      // A daemon too old to pass `details` through. Say so rather than rendering an empty
      // folder — "nothing here" and "I could not ask" are different answers.
      throw new Error(
        'list_workflows returned no structured details — this daemon predates ' +
          'tools.invoke returning them. Restart it after updating.',
      )
    }
    return found as WorkflowEntry[]
  }, [client])
}

// ---------------------------------------------------------------------------
// Settings — an agent app configures ITSELF.
// ---------------------------------------------------------------------------
// `config.get` / `config.set` are open to an app window on purpose. The alternative is an agent
// that fails because a field is empty and has no way to say which field, in the one window the
// user is actually looking at — they have to find the daemon's own UI to fix it.
//
// The page speaks in the AUTHOR's key names (`COMFY_URL`). Storage prefixes them per agent so two
// agents can hold different values for the same name; the page never learns that prefix exists.
//
// SECRETS ARE WRITE-ONLY. `settingsValues` carries the non-secret fields so a typo in a URL can
// be corrected instead of retyped; a secret comes back only as a presence boolean in `env`. That
// asymmetry is the design — an app page has no CSP, so anything readable is exfiltratable.

export interface SettingField {
  key: string
  label: string
  kind: 'text' | 'url' | 'secret'
  required: boolean
  help: string
}

/** THIS AGENT'S ID, and it must match the folder `agent.toml` sits in — the daemon derives it
 *  from there. The settings page needs it because the two layers are keyed by it: `agents.<id>.model`
 *  overrides the daemon's `model`, and a page that guessed wrong would silently edit another
 *  agent's layer while showing this one's values. */
export const AGENT_ID = 'comfy-smith'

export interface SettingsSurface {
  settings: SettingField[]
  settingsValues: Record<string, string>
  env: Record<string, boolean>
  providerKeys: string[]
  keysLocked: boolean
  effectiveModel: string
  version: string
}

export function useSettings(client: AgentdClient) {
  const [data, setData] = useState<SettingsSurface | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await client.request<SettingsSurface>('config.get')
      setData(res)
      setError('')
    } catch (e) {
      // Surfaced, not swallowed: a settings page that renders empty on a failed read looks
      // exactly like one where nothing is configured, and the user "fixes" it by retyping
      // values that were already there.
      setError(`could not read settings: ${String(e)}`)
    }
  }, [client])

  useWhenOpen(client, load)

  // NO `save` HERE. Writing belongs to the shared settings page (`common/settings`), which owns
  // the edit buffer and the refusal messages. This hook is what the REST of the window reads:
  // the server URL in the top bar, and whether a required field is still empty. `reload` is
  // exported so the page can say when it saved — see `onSaved` in App.tsx.
  return { data, error, reload: load }
}

// ---------------------------------------------------------------------------
// Who is signed in, and whose keys pay.
// ---------------------------------------------------------------------------
// BOTH FACTS BELONG TO THIS CLIENT, not to the daemon. A daemon-side session is one slot, and
// one slot cannot serve two people: the second to sign in overwrites the first, signing out
// signs out everybody, and one window's Cloud switch moves every other window's billing. The
// client stores both and presents them on each connection, which is why changing either has to
// RECONNECT — the daemon reads them when the socket opens.
//
// The SDK owns the mechanism (auth.ts). This hook is only React state around it.

export type { AuthState, RunMode }

export function useAuth(client: AgentdClient) {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    void (async () => {
      try {
        setAuth(await authStatus({ client }))
        setError('')
      } catch (e) {
        setError(String(e))
      }
    })()
  }, [client])

  // Not `useWhenOpen`: sign-in state is read over ORDINARY HTTP against the daemon, not over the
  // socket, so it is answerable before the socket is up — and it is what explains why the socket
  // might not come up at all.
  useEffect(() => load(), [load])

  const run = useCallback(async (fn: () => Promise<AuthState>) => {
    setBusy(true)
    setError('')
    try {
      setAuth(await fn())
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  /* Sign-in is a CARD the app renders (common/auth/SignIn), not a gate that paints itself over
     the page. This only raises the flag; App shows `<SignIn onDone={signedIn}>` while it is up. */
  const [wantsSignIn, setWantsSignIn] = useState(false)
  const signIn = useCallback(() => setWantsSignIn(true), [])
  const signedIn = useCallback(() => {
    setWantsSignIn(false)
    void load()
  }, [load])
  const signOut = useCallback(() => run(() => authLogout({ client })), [run, client])
  const chooseMode = useCallback(
    (mode: RunMode) => run(() => setRunMode(mode, { client })),
    [run, client],
  )

  return { auth, busy, error, signIn, wantsSignIn, signedIn, signOut, chooseMode, reload: load }
}

/** Declared MCP servers and why any of them is not usable.
 *
 *  The answer to "why does this agent have no tools", which otherwise exists nowhere — the model
 *  just says it cannot do the thing. Empty for an agent that declares none. */
export function useMcpStatus(client: AgentdClient) {
  const [servers, setServers] = useState<any[]>([])
  const load = useCallback(() => {
    void (async () => {
      try {
        const res: any = await client.request('mcp.status')
        setServers(res?.servers ?? [])
      } catch {
        // A daemon without declared-MCP support answers with an error; there is simply nothing
        // to show, and an error banner about a feature this agent does not use would be noise.
      }
    })()
  }, [client])
  useWhenOpen(client, load)
  return servers
}

/** Past conversations with THIS agent, newest first.
 *
 *  No agentId argument: the daemon scopes this connection to our own agent, so asking for
 *  "the sessions" already means ours — and naming it would be a second copy of the id to keep
 *  in sync with agent.toml.
 */
export interface SessionRow {
  sessionId: string
  title?: string
  updatedAt?: string
  preview?: string
}

export function useSessions(client: AgentdClient, refreshOn: unknown) {
  const [sessions, setSessions] = useState<SessionRow[]>([])

  const load = useCallback(async () => {
    try {
      const res: any = await client.sessions()
      setSessions(res?.sessions ?? [])
    } catch {
      // The list is a convenience; the chat works without it and an error banner here would
      // be louder than the feature is important.
    }
  }, [client])

  useWhenOpen(client, load)

  // `refreshOn` is the caller's "something happened" signal (a new thread, a run finishing).
  // Guarded on `connected`, or it fires the same doomed request useWhenOpen exists to avoid.
  useEffect(() => {
    if (client.connected) void load()
  }, [client, load, refreshOn])

  // The daemon broadcasts when any client changes a session. Without this, a rename made in the
  // main agentd window leaves this list showing the old title until something else reloads it.
  useEffect(() => client.on('sessions.changed', () => void load()), [client, load])

  /** Both of these REPORT their refusal. The gateway answers a refused delete with
   *  `{ok: false, error}` on a successful frame — a caller that only catches rejections shows a
   *  row vanishing from the UI and reappearing on the next reload, with no explanation. */
  const rename = useCallback(
    async (sessionId: string, title: string) => {
      const res: any = await client.request('sessions.rename', { sessionKey: sessionId, title })
      if (res?.ok === false) throw new Error(String(res.error || 'rename refused'))
      await load()
    },
    [client, load],
  )

  const remove = useCallback(
    async (sessionId: string) => {
      const res: any = await client.request('sessions.delete', { sessionKey: sessionId })
      if (res?.ok === false) throw new Error(String(res.error || 'delete refused'))
      await load()
    },
    [client, load],
  )

  /** Fork: copy a conversation into a new one and return its key.
   *
   *  The point is the CONTEXT. A long thread is an expensive thing to build — the server was
   *  inspected, the models were checked, the constraints were argued out — and the only way to
   *  try a different direction used to be to continue in it (losing the known-good state) or
   *  start fresh (losing everything). A fork keeps both. */
  const fork = useCallback(
    async (sessionId: string): Promise<string> => {
      const res: any = await client.request('sessions.duplicate', { sessionKey: sessionId })
      if (res?.ok === false || !res?.sessionKey) {
        throw new Error(String(res?.error || 'fork refused'))
      }
      await load()
      return String(res.sessionKey)
    },
    [client, load],
  )

  return { sessions, reload: load, rename, remove, fork }
}
