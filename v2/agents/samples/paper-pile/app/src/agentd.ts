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

export interface ToolRow {
  id: string
  name: string
  done: boolean
  ok: boolean
  detail: string
}

export interface Turn {
  role: 'user' | 'assistant'
  text: string
  tools: ToolRow[]
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
  const sessionRef = useRef<string>(`chat-${Math.random().toString(36).slice(2, 10)}`)
  const onToolDone = useRef(opts.onToolDone)
  onToolDone.current = opts.onToolDone

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
          if (event.kind !== 'text_delta') return // NOT 'message_delta' — no such event
          const delta = String(event.delta ?? '')  // `delta`, verified — not `text`
          if (!delta) return
          setTurns((prev) => {
            const next = [...prev]
            const last = next[next.length - 1]
            if (last?.role === 'assistant' && last.streaming) {
              next[next.length - 1] = { ...last, text: last.text + delta }
            } else {
              next.push({ role: 'assistant', text: delta, tools: [], streaming: true })
            }
            return next
          })
          return
        }
        case 'tool_execution_start': {
          const row: ToolRow = {
            id: String(event.toolCallId ?? event.toolName ?? Math.random()),
            name: String(event.toolName ?? 'tool'),
            done: false,
            ok: true,
            detail: '',
          }
          setTurns((prev) => withCurrentAssistant(prev, (t) => ({ ...t, tools: [...t.tools, row] })))
          return
        }
        case 'tool_execution_end': {
          const id = String(event.toolCallId ?? '')
          const name = String(event.toolName ?? '')
          const ok = !event.isError
          setTurns((prev) =>
            withCurrentAssistant(prev, (t) => ({
              ...t,
              tools: t.tools.map((r) =>
                r.id === id || (!id && r.name === name && !r.done)
                  ? { ...r, done: true, ok, detail: String(event.summary ?? '') }
                  : r,
              ),
            })),
          )
          // Tell the rest of the app something changed. Named, so a panel can ignore tools it
          // does not care about.
          if (name) onToolDone.current?.(name)
          return
        }
        case 'agent_end': {
          setBusy(false)
          setTurns((prev) => withCurrentAssistant(prev, (t) => ({ ...t, streaming: false })))
          return
        }
      }
    })
  }, [client])

  const ask = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy) return
      setTurns((prev) => [...prev, { role: 'user', text: message, tools: [], streaming: false }])
      setBusy(true)
      try {
        // No agentId — the daemon scopes this connection to our own agent already.
        await client.send({ message, sessionKey: sessionRef.current })
      } catch (e) {
        setBusy(false)
        setTurns((prev) => [
          ...prev,
          { role: 'assistant', text: `could not send: ${String(e)}`, tools: [], streaming: false },
        ])
      }
    },
    [client, busy],
  )

  const reset = useCallback(() => {
    sessionRef.current = `chat-${Math.random().toString(36).slice(2, 10)}`
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
    setTurns(historyToTurns(messages))
    setBusy(false)
  }, [])

  return { turns, busy, ask, reset, resume, sessionKey: sessionRef.current }
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
    out.push({ role, text, tools: [], streaming: false })
  }
  return out
}

function withCurrentAssistant(turns: Turn[], fn: (t: Turn) => Turn): Turn[] {
  const next = [...turns]
  const last = next[next.length - 1]
  if (last?.role === 'assistant' && last.streaming) {
    next[next.length - 1] = fn(last)
  } else {
    next.push(fn({ role: 'assistant', text: '', tools: [], streaming: true }))
  }
  return next
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

  // WAIT FOR THE SOCKET. `fromPage()` returns a client immediately but the connection is still
  // opening, so a request on mount fails with "not connected" — and because nothing retries, the
  // panel keeps showing that error long after the daemon is reachable.
  useEffect(() => {
    if (ready) void reload()
  }, [ready, reload])

  return { rows, error, reload, history, rename, remove }
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
