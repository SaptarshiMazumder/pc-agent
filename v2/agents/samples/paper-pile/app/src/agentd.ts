/* The daemon, as React hooks. THIS FILE IS THE PART WORTH COPYING.
 *
 * Every agent app is a client of the same daemon over one WebSocket, and the same three
 * mistakes get made when it is written from scratch:
 *
 *   1. the run-event payload is NESTED — `payload.event.type`, not `payload.type`
 *   2. streamed text is `message_update` / `text_delta`; `message_delta` does not exist
 *   3. the socket outlives React, so effects must unsubscribe or handlers stack up per render
 *
 * All three are invisible at runtime: the socket connects, the console stays clean, and the
 * screen never updates. Hooks are the fix — subscribe once, clean up on unmount, and the wrong
 * event name becomes a compile error rather than silence.
 */

import { fromPage, resultText, type AgentdClient } from '@agentd/client'
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

/** ONE turn is an ORDERED LIST OF BLOCKS.
 *
 *  THIS LOOKS LIKE A DETAIL AND IS NOT. Two parallel fields — `text` plus `tools[]` — throw away
 *  the order in which things actually happened, and the only thing such a shape can render is
 *  "every tool, then every word": a wall of tool names with unrelated sentences fused underneath.
 *  Reasoning, answers and tool calls interleave in real runs, and a transcript that cannot show
 *  that is misreporting what the agent did.
 *
 *  One array, appended in arrival order, and the screen matches the run. */
export type Block = TextBlock | ThinkingBlock | ToolBlock | NoteBlock

export interface TextBlock {
  kind: 'text'
  text: string
}

/** The model's reasoning. Streamed, because a long research phase with nothing on screen but a
 *  motionless list of tool names reads as a hang. */
export interface ThinkingBlock {
  kind: 'thinking'
  text: string
}

export interface ToolBlock {
  kind: 'tool'
  id: string
  name: string
  done: boolean
  ok: boolean
  detail: string
  /** From `tool_progress` — what distinguishes a slow tool from a stuck one. */
  progress: string
}

/** Something the RUN said about itself: a model fallback, or the provider's own failure text.
 *  Dropped, every failure looks like the same shrug and a dead key is indistinguishable from a
 *  rate limit. */
export interface NoteBlock {
  kind: 'note'
  tone: 'info' | 'error'
  text: string
}

export interface Turn {
  role: 'user' | 'assistant'
  blocks: Block[]
  streaming: boolean
}

/** A conversation: history, streaming text, and the live tool rows for the current turn.
 *
 *  `onToolDone` is the hook that keeps the rest of the screen honest — when the agent finishes
 *  a tool IN CHAT, whatever that tool changed should refresh. Without it a panel shows the state
 *  from before the agent acted, and the user is looking at a lie with no way to know. */
export function useChat(client: AgentdClient, opts: { onToolDone?: (name: string) => void } = {}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  // THE KEY OUTLIVES THE PAGE. A fresh random key per mount means reloading the window silently
  // abandons the conversation you were in: the daemon still has it, the history rail can still
  // open it, but the thread on screen is a new one and the agent answers with none of the context
  // above it. Persisted, a reload resumes.
  const sessionRef = useRef<string>(loadSessionKey())
  // Resolves the promise `ask` handed back, when the RUN ends — see `ask`.
  const finish = useRef<(() => void) | null>(null)
  const onToolDone = useRef(opts.onToolDone)
  onToolDone.current = opts.onToolDone

  // Restore what was already said in the persisted session. Without this the key survives the
  // reload but the screen does not, which reads as "it forgot" even though the next question
  // continues the thread correctly.
  useEffect(() => {
    let live = true
    client
      .history(sessionRef.current)
      .then((res: any) => {
        if (!live) return
        const restored = historyToTurns(res?.messages ?? [])
        if (restored.length) setTurns(restored)
      })
      .catch(() => {
        // A session the daemon has never heard of is the normal first-run case, not a failure.
      })
    return () => {
      live = false
    }
  }, [client])

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
          // Reasoning and answer arrive on the SAME event under different kinds. Appending both
          // in arrival order keeps a thought that preceded a tool call before it.
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
          // A slow tool and a stuck one look identical without this.
          const text = String(event.text ?? '').trim()
          if (!text) return
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) =>
              updateTool(
                t,
                (b) => !b.done,
                (b) => ({ ...b, progress: text }),
              ),
            ),
          )
          return
        }
        case 'tool_execution_end': {
          const id = String(event.toolCallId ?? '')
          const name = String(event.toolName ?? '')
          const ok = !event.isError
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) =>
              updateTool(
                t,
                (b) => b.id === id || (!id && b.name === name && !b.done),
                (b) => ({ ...b, done: true, ok, detail: String(event.summary ?? '') }),
              ),
            ),
          )
          // Tell the rest of the app something changed. Named, so a panel can ignore tools it
          // does not care about.
          if (name) onToolDone.current?.(name)
          return
        }
        case 'model_fallback': {
          // The configured model did not answer. Silence here reads as "the agent is just worse
          // today"; the names are what let someone act on it.
          const from = String(event.from ?? 'the configured model')
          const to = String(event.to ?? 'a fallback model')
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) => ({
              ...t,
              blocks: [
                ...t.blocks,
                {
                  kind: 'note',
                  tone: 'info',
                  text: from + ' was unavailable — answered with ' + to + '.',
                } as NoteBlock,
              ],
            })),
          )
          return
        }
        case 'agent_end': {
          setBusy(false)
          const failure = String(event.error ?? '').trim()
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) => ({
              ...t,
              streaming: false,
              // THE PROVIDER'S OWN WORDS. A dead key, a rate limit and an empty balance are three
              // different problems with three different fixes, and only this text tells them apart.
              blocks: failure
                ? [...t.blocks, { kind: 'note', tone: 'error', text: failure } as NoteBlock]
                : t.blocks,
            })),
          )
          finish.current?.()
          finish.current = null
          return
        }
      }
    })
  }, [client])

  const ask = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy) return
      setTurns((prev) => [
        ...prev,
        { role: 'user', blocks: [{ kind: 'text', text: message }], streaming: false },
      ])
      setBusy(true)
      try {
        // `chat.send` RETURNS AS SOON AS THE RUN IS ACCEPTED — it answers {runId} straight after
        // create_task, not when the agent has finished. Awaiting only the RPC therefore means
        // "the message was delivered", and a caller that treats that as "the work is done" marks
        // a document ingested before anything has read it. So the promise this returns is settled
        // by `agent_end` instead.
        const done = new Promise<void>((resolve) => {
          finish.current = resolve
        })
        // No agentId — the daemon scopes this connection to our own agent already.
        await client.send({ message, sessionKey: sessionRef.current })
        await done
      } catch (e) {
        finish.current = null
        setBusy(false)
        setTurns((prev) => [
          ...prev,
          {
            role: 'assistant',
            blocks: [{ kind: 'note', tone: 'error', text: `could not send: ${String(e)}` }],
            streaming: false,
          },
        ])
      }
    },
    [client, busy],
  )

  /** Stop the run in progress. Settles whatever is awaiting `ask`, so a caller driving a batch
   *  is released rather than left waiting on a turn that will never end. */
  const abort = useCallback(async () => {
    try {
      await client.abort(sessionRef.current)
    } finally {
      finish.current?.()
      finish.current = null
      setBusy(false)
    }
  }, [client])

  const reset = useCallback(() => {
    finish.current?.()  // never leave a caller awaiting a session we just abandoned
    finish.current = null
    sessionRef.current = newSessionKey()
    setTurns([])
    setBusy(false)
  }, [])

  /** Continue a saved conversation: adopt its key, and show what was already said.
   *
   *  Adopting the KEY is the part that matters — render the old messages without it and the next
   *  question starts a brand-new thread that merely looks like a continuation, so the agent
   *  answers with no knowledge of anything above it on screen. */
  const resume = useCallback((sessionKey: string, messages: any[]) => {
    sessionRef.current = sessionKey
    rememberSessionKey(sessionKey)
    setTurns(historyToTurns(messages))
    setBusy(false)
  }, [])

  return { turns, busy, ask, abort, reset, resume, sessionKey: sessionRef.current }
}

/** Where the conversation key lives across reloads. Per agent app, so two agent windows open
 *  side by side do not adopt each other's thread. */
const SESSION_STORAGE_KEY = 'paper-pile:session'

function newSessionKey(): string {
  const key = `chat-${Math.random().toString(36).slice(2, 10)}`
  rememberSessionKey(key)
  return key
}

function rememberSessionKey(key: string): void {
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, key)
  } catch {
    // Storage can be unavailable (private mode, a locked-down embed). The app still works — the
    // conversation simply stops surviving reloads, which is exactly the old behaviour.
  }
}

function loadSessionKey(): string {
  try {
    const saved = localStorage.getItem(SESSION_STORAGE_KEY)
    if (saved) return saved
  } catch {
    /* see rememberSessionKey */
  }
  return newSessionKey()
}

/** Stored wire messages -> the shape this app renders.
 *
 *  `content` is a string on some messages and a list of typed blocks on others (the model's own
 *  turns carry tool_use blocks alongside text). Only text blocks are shown; a tool call replayed
 *  as raw JSON would be noise nobody asked for. */
export function historyToTurns(messages: any[]): Turn[] {
  const out: Turn[] = []
  for (const m of messages ?? []) {
    const role = m?.role === 'user' ? 'user' : 'assistant'
    const raw = m?.content
    const text =
      typeof raw === 'string'
        ? raw
        : Array.isArray(raw)
          ? raw
              // ONLY `type === 'text'`. A tool_result block also carries a `.text`, so a looser
              // check replays whole file dumps as if the agent had said them — a resumed chat
              // then opens with 2,000 numbered lines of a skill file.
              .filter((b: any) => b?.type === 'text' && typeof b?.text === 'string')
              .map((b: any) => String(b.text ?? ''))
              .join('')
          : ''
    if (!text.trim()) continue
    out.push({ role, blocks: [{ kind: 'text', text }], streaming: false })
  }
  return out
}

function withCurrentAssistant(turns: Turn[], fn: (t: Turn) => Turn): Turn[] {
  const next = [...turns]
  const last = next[next.length - 1]
  if (last?.role === 'assistant' && last.streaming) {
    next[next.length - 1] = fn(last)
  } else {
    next.push(fn({ role: 'assistant', blocks: [], streaming: true }))
  }
  return next
}

/** Append to the LAST block when it is the same kind, else start a new one. That is what keeps
 *  text written after a tool call from being merged back into the paragraph before it. */
function appendDelta(turn: Turn, kind: 'text' | 'thinking', delta: string): Turn {
  const last = turn.blocks[turn.blocks.length - 1]
  if (last && last.kind === kind) {
    return { ...turn, blocks: [...turn.blocks.slice(0, -1), { ...last, text: last.text + delta }] }
  }
  return { ...turn, blocks: [...turn.blocks, { kind, text: delta }] }
}

function updateTool(
  turn: Turn,
  match: (b: ToolBlock) => boolean,
  change: (b: ToolBlock) => ToolBlock,
): Turn {
  return {
    ...turn,
    blocks: turn.blocks.map((b) => (b.kind === 'tool' && match(b) ? change(b) : b)),
  }
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

/** Files the agent has produced, from its own workspace. */
export function useWorkspace(client: AgentdClient) {
  return useCallback(
    async (path = ''): Promise<Array<{ name: string; path: string; size: number }>> => {
      const res: any = await client.request('workspace.list', path ? { path } : {})
      return (res?.entries ?? res?.files ?? []) as Array<{ name: string; path: string; size: number }>
    },
    [client],
  )
}

/* ---------------------------------------------------------------------------------------------
 * The rest of the daemon: history, settings and files.
 *
 * These are ordinary `client.request` calls, and every shape below was read off the gateway
 * rather than guessed — the field names are the ones it actually sends.
 *
 * Each takes `ready` — pass `status === 'open'`. It is a parameter rather than something the hook
 * works out for itself so the dependency is visible at the call site: these panels CANNOT load
 * before the socket does, and firing anyway paints an error on a healthy app.
 * ------------------------------------------------------------------------------------------ */

export interface SessionRow {
  sessionId: string
  title: string
  snippet: string
  messages: number
  modified: number
  projectId: string
}

/** Saved conversations for THIS agent.
 *
 *  The list calls them `sessionId`; `sessions.history` wants a `sessionKey` and accepts the id
 *  under either name. Passing the row's id straight through is correct — the alias is in the
 *  daemon, verified, not assumed. */
export function useSessions(client: AgentdClient, ready: boolean) {
  const [rows, setRows] = useState<SessionRow[]>([])
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    try {
      const res: any = await client.request('sessions.list', {})
      setRows((res?.sessions ?? []) as SessionRow[])
      setError('')
    } catch (e) {
      setError(String((e as Error)?.message ?? e))
    }
  }, [client])

  const history = useCallback(
    async (sessionId: string) => {
      const res: any = await client.request('sessions.history', { sessionKey: sessionId })
      return (res?.messages ?? []) as Array<any>
    },
    [client],
  )

  const rename = useCallback(
    async (sessionId: string, title: string) => {
      await client.request('sessions.rename', { sessionKey: sessionId, title })
      await reload()
    },
    [client, reload],
  )

  const remove = useCallback(
    async (sessionId: string) => {
      await client.request('sessions.delete', { sessionKey: sessionId })
      await reload()
    },
    [client, reload],
  )

  /** Copy a conversation and return the new key.
   *
   *  A long thread is expensive to build. Without a fork the only ways to try a different
   *  direction are to continue in it (losing the known-good state) or start fresh (losing the
   *  context) — so the useful move is the one that is missing. The caller must OPEN the copy: a
   *  fork the user does not land in is indistinguishable from one that did nothing. */
  const fork = useCallback(
    async (sessionId: string): Promise<string> => {
      const res: any = await client.request('sessions.duplicate', { sessionKey: sessionId })
      await reload()
      return String(res?.sessionKey || '')
    },
    [client, reload],
  )

  // WAIT FOR THE SOCKET. `fromPage()` returns a client immediately but the connection is still
  // opening, so a request on mount fails with "not connected" — and because nothing retries, the
  // panel keeps showing that error long after the daemon is reachable.
  useEffect(() => {
    if (ready) void reload()
  }, [ready, reload])

  return { rows, error, reload, history, rename, remove, fork }
}

export interface SettingField {
  key: string
  label: string
  kind: string
  required: boolean
  help: string
}

/** This agent's own declared [[settings]] — the page that was missing.
 *
 *  THE DECLARATION SHIPS, THE VALUES DO NOT. `config.get` returns the SHAPE the author declared
 *  plus the current values of the non-secret ones; a secret is reported only as present or absent
 *  (`env[KEY]`), never read back.
 *
 *  Saving sends BARE key names. The daemon prefixes them per agent and refuses any name this
 *  agent did not declare — so the app cannot reach another agent's credentials even by asking. */
export function useSettings(client: AgentdClient, ready: boolean) {
  const [fields, setFields] = useState<SettingField[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [present, setPresent] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    try {
      const res: any = await client.request('config.get', {})
      setFields((res?.settings ?? []) as SettingField[])
      setValues((res?.settingsValues ?? {}) as Record<string, string>)
      setPresent((res?.env ?? {}) as Record<string, boolean>)
      setError('')
    } catch (e) {
      setError(String((e as Error)?.message ?? e))
    }
  }, [client])

  /** Returns "" on success, or the daemon's refusal.
   *
   *  `config.set` reports a refusal as DATA — `{saved: false, error}` — not as a thrown error. A
   *  caller that only catches exceptions shows "Saved" for a save that did not happen, and the
   *  user goes on believing a key is stored. */
  const save = useCallback(
    async (patch: Record<string, string>): Promise<string> => {
      try {
        const res: any = await client.request('config.set', { keys: patch })
        if (res?.saved === false) return String(res?.error || 'the daemon refused the save')
        await reload()
        return ''
      } catch (e) {
        return String((e as Error)?.message ?? e)
      }
    },
    [client, reload],
  )

  // WAIT FOR THE SOCKET. `fromPage()` returns a client immediately but the connection is still
  // opening, so a request on mount fails with "not connected" — and because nothing retries, the
  // panel keeps showing that error long after the daemon is reachable.
  useEffect(() => {
    if (ready) void reload()
  }, [ready, reload])

  return { fields, values, present, error, reload, save }
}

export interface FileRow {
  name: string
  kind: string
  size: number
  modified: number
  rel: string
  path: string
}

/** Files in the agent's workspace.
 *
 *  `workspace.list` reports failure as `{entries: [], error}` — an error that arrives as DATA. An
 *  unreadable workspace and an empty one are the same payload apart from that field, so it is
 *  surfaced; without it the panel says "no files" for a workspace it simply could not read. */
export function useFiles(client: AgentdClient, ready: boolean) {
  const [entries, setEntries] = useState<FileRow[]>([])
  const [error, setError] = useState('')
  const [path, setPath] = useState('')

  const reload = useCallback(
    async (next = path) => {
      try {
        const res: any = await client.request('workspace.list', next ? { path: next } : {})
        setEntries((res?.entries ?? []) as FileRow[])
        setError(res?.error ? String(res.error) : '')
        setPath(next)
      } catch (e) {
        setError(String((e as Error)?.message ?? e))
      }
    },
    [client, path],
  )

  const remove = useCallback(
    async (rel: string) => {
      await client.request('workspace.delete', { path: rel })
      await reload()
    },
    [client, reload],
  )

  useEffect(() => {
    if (ready) void reload('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, ready])

  return { entries, error, path, reload, remove }
}
