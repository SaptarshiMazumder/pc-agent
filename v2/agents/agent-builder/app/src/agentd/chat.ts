/* The conversation with Agent Builder.
 *
 * A turn is an ORDERED LIST OF ITEMS — prose, reasoning, tool rows — appended in the order the
 * events arrive. The vanilla window got this right by construction, because it appended DOM
 * nodes; a React port that stores `{text, tools[]}` instead would render every tool above every
 * sentence and silently destroy the sequence. Watching the order IS how you watch an agent being
 * built, so the order is the data structure.
 *
 * Tool activity is shown as it happens. When a tool finishes, `onToolDone` fires so the inspector
 * re-reads the tree and the new file appears (and flashes).
 */

import type { AgentdClient, Attachment } from '@agentd/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AGENT_ID } from './client'
import type { AgentRow } from './roster'

export interface ScopeItem {
  kind: 'scope'
  id: string
  name: string
}
export interface UserItem {
  kind: 'user'
  text: string
  files: Attachment[]
}
export interface BotItem {
  kind: 'bot'
  text: string
  /** Still being written into — draws the caret, and the next delta appends here. */
  streaming: boolean
}
export interface ThinkItem {
  kind: 'think'
  text: string
}
export interface ToolItem {
  kind: 'tool'
  id: string
  name: string
  args: string
  done: boolean
  error: boolean
}
export interface FallbackItem {
  kind: 'fallback'
  from: string
  to: string
  reason: string
}

export type ThreadItem = ScopeItem | UserItem | BotItem | ThinkItem | ToolItem | FallbackItem

const MAX_FILES = 10

/** A clipboard image usually has no usable filename ('' or no extension). The daemon then stores
 *  it as literally "attachment" — which, having no extension, is not classified as an image, so a
 *  vision model never receives it as one. Name it from its mime type. */
function attachmentName(f: File): string {
  if (f.name && f.name.includes('.')) return f.name
  const ext = (f.type.split('/')[1] || 'bin').split('+')[0].replace(/[^a-z0-9]/gi, '')
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  return `${f.name || 'pasted'}-${stamp}.${ext}`
}

function readFile(f: File): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () =>
      resolve({
        name: attachmentName(f),
        mimeType: f.type || 'application/octet-stream',
        dataBase64: String(r.result).split(',')[1] || '',
      })
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
}

/** One line describing what a tool is doing.
 *
 *  Tool arguments have no common shape, so this picks the most identifying SCALAR it recognises.
 *  The "scalar" part is load-bearing: this used to end with `String(Object.values(args)[0])`, and
 *  for a tool whose first argument is a list — a plan, a batch of files — that renders
 *  "[object Object],[object Object]". Which one you got depended on the key order in the model's
 *  JSON, so the same tool looked fine on one call and broken on the next.
 *
 *  `explanation` is in the list for `update_plan`, whose whole point is the note about what just
 *  changed — it is the most useful thing that tool can show while a build runs. */
function describe(v: unknown): string {
  if (v == null) return ''
  if (typeof v !== 'object') return String(v)
  if (Array.isArray(v)) {
    // A checklist: the step being worked on is the useful line, not the whole list.
    const label = (x: any) => x && (x.step || x.title || x.name || x.text)
    const doing = v.find((x: any) => x && typeof x === 'object' && x.status === 'in_progress')
    if (doing && label(doing)) return String(label(doing))
    if (v.length && typeof v[0] !== 'object') return v.join(', ')
    return `${v.length} item${v.length === 1 ? '' : 's'}`
  }
  return '' // a nested object says nothing useful in one line
}

export function summarize(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  const record = args as Record<string, unknown>
  for (const k of ['agent_id', 'id', 'path', 'name', 'query', 'file', 'explanation']) {
    const s = describe(record[k])
    if (s) return s.slice(0, 70)
  }
  for (const v of Object.values(record)) {
    const s = describe(v)
    if (s) return s.slice(0, 70)
  }
  return ''
}

/** What the model is told about the subject, the first time we send.
 *
 *  It goes in the MESSAGE because chat.send has no system/context parameter — and it is SHOWN,
 *  because a client that silently prepends instructions to your words leaves you unable to
 *  explain the model's behaviour later. The last line is load-bearing: without it, "add pagination
 *  to the job finder" has previously been answered by building a second job finder. */
function preamble(agent: AgentRow): string {
  return (
    `[context] We are working on the EXISTING agent \`${agent.id}\`, which lives at ` +
    `\`agents/${agent.id}/\`. Read its agent.toml, IDENTITY.md, AGENTS.md and any ` +
    `skills/, plugins/ and ui/ before proposing changes, so you are working from what is ` +
    `actually there. Do NOT create a new agent unless I explicitly ask for one.`
  )
}

const newSessionKey = () => `builder-${Date.now().toString(36)}`

export function useChat(
  client: AgentdClient,
  opts: { onToolDone?: () => void; onSession?: (key: string) => void } = {},
) {
  const [items, setItems] = useState<ThreadItem[]>([])
  const [running, setRunning] = useState(false)
  const [pending, setPending] = useState<Attachment[]>([])
  const [sessionKey, setSessionKey] = useState(newSessionKey)

  const sessionRef = useRef(sessionKey)
  sessionRef.current = sessionKey

  // The agent this conversation is ABOUT (null = we are creating something new). Carried into the
  // FIRST message only; after that it is in the transcript and repeating it is noise.
  const scopeRef = useRef<AgentRow | null>(null)
  const scopeSentRef = useRef(false)

  const callbacks = useRef(opts)
  callbacks.current = opts

  // ── the event stream ──────────────────────────────────────────────────────
  useEffect(() => {
    /** Append to the bubble being streamed into, or start a new one.
     *
     *  A tool row (or anything else) landing in between ends the run of deltas, so the next one
     *  opens a fresh bubble BELOW it. That is the whole ordering rule. */
    const appendTo = (kind: 'bot' | 'think', delta: string) =>
      setItems((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (kind === 'bot') {
          if (last?.kind === 'bot' && last.streaming) {
            next[next.length - 1] = { ...last, text: last.text + delta }
          } else {
            next.push({ kind: 'bot', text: delta, streaming: true })
          }
        } else if (last?.kind === 'think') {
          next[next.length - 1] = { ...last, text: last.text + delta }
        } else {
          next.push({ kind: 'think', text: delta })
        }
        return next
      })

    /** Commit whatever is streaming and drop the caret. Called whenever the assistant STOPS
     *  writing prose — a tool starting counts, and forgetting it left a blinking caret stranded
     *  on every bubble that was interrupted by a tool call. */
    const settle = () =>
      setItems((prev) =>
        prev.map((it, i) =>
          i === prev.length - 1 && it.kind === 'bot' && it.streaming
            ? { ...it, streaming: false }
            : it,
        ),
      )

    return client.on('chat.event', (payload: any) => {
      if (payload?.sessionKey !== sessionRef.current) return
      const ev = payload?.event
      if (!ev) return

      switch (ev.type) {
        case 'message_update': {
          if (ev.kind === 'thinking_delta') appendTo('think', String(ev.delta || ''))
          else if (ev.kind === 'text_delta') appendTo('bot', String(ev.delta || ''))
          return
        }
        case 'tool_execution_start': {
          // The assistant stopped writing to start a tool. Settle the bubble (and lose the caret)
          // — the next delta opens a fresh one below the tool row.
          settle()
          setItems((prev) => [
            ...prev,
            {
              kind: 'tool',
              id: String(ev.toolCallId || ev.toolName || Math.random()),
              name: String(ev.toolName || '?'),
              args: summarize(ev.args),
              done: false,
              error: false,
            },
          ])
          return
        }
        case 'tool_execution_end': {
          const id = String(ev.toolCallId || ev.toolName || '')
          setItems((prev) => {
            const next = [...prev]
            // Last match wins: a tool called twice in one turn has two rows with the same name,
            // and the running one is always the later.
            for (let i = next.length - 1; i >= 0; i--) {
              const it = next[i]
              if (it.kind === 'tool' && it.id === id && !it.done) {
                next[i] = { ...it, done: true, error: !!ev.isError }
                return next
              }
            }
            return prev
          })
          // EVERY tool, not a list of the ones that write. This used to test the name against
          // /^(write|edit|create_agent|...)$/ — and when scaffold_ui was added nobody extended it,
          // so a whole generated ui/ folder could land with the tree showing none of it. A re-read
          // is a handful of small directory listings; a list you must remember to extend is a bug
          // waiting for the next tool.
          callbacks.current.onToolDone?.()
          return
        }
        // A run is MANY turns — the model answers, calls a tool, answers again. `turn_end` fires
        // after each one, so it must only settle the current bubble; treating it as the end would
        // flip the composer back to idle while the run is still going.
        case 'message_end':
        case 'turn_end': {
          settle()
          return
        }
        // The configured model could not answer and another one took over. Never silent: "the
        // model you chose is not the one replying" is the fact that turns an unpaid API key from
        // a mystery into a one-line fix.
        case 'model_fallback': {
          settle()
          setItems((prev) => [
            ...prev,
            {
              kind: 'fallback',
              from: String(ev.from || '?'),
              to: String(ev.to || '?'),
              reason: String(ev.reason || '').slice(0, 120),
            },
          ])
          return
        }
        // `agent_end` is the run terminal (stopReason, and `error` when it failed).
        case 'agent_end': {
          setRunning(false)
          settle()
          if (ev.error) {
            setItems((prev) => [
              ...prev,
              { kind: 'bot', text: `**Run failed.** ${ev.error}`, streaming: false },
            ])
          }
          return
        }
        case 'error': {
          setRunning(false)
          setItems((prev) => [
            ...prev,
            {
              kind: 'bot',
              text: `**Error.** ${ev.message || 'the run failed'}`,
              streaming: false,
            },
          ])
          return
        }
      }
    })
  }, [client])

  // ── attachments ───────────────────────────────────────────────────────────
  // Screenshots are how you show an agent what is wrong with an agent, so this window needs them.
  // Three routes, because people reach for all three: paste, drag-drop, and a button.
  const addFiles = useCallback(async (list: FileList | File[] | null) => {
    const files = Array.from(list || [])
    if (!files.length) return
    const read = await Promise.all(files.map(readFile))
    setPending((prev) => [...prev, ...read].slice(0, MAX_FILES))
  }, [])

  const removeFile = useCallback(
    (index: number) => setPending((prev) => prev.filter((_, i) => i !== index)),
    [],
  )

  // ── sending ───────────────────────────────────────────────────────────────
  const send = useCallback(
    async (text: string) => {
      const body = text.trim()
      // a message may be attachments-only — the daemon accepts that, so don't require text
      if ((!body && !pending.length) || running) return

      const sending = pending
      setPending([])
      setItems((prev) => [...prev, { kind: 'user', text: body, files: sending }])
      setRunning(true)

      const scope = scopeRef.current
      const carry = scope && !scopeSentRef.current
      // Marked BEFORE the await, not after: everything up to the await runs synchronously, so a
      // flag set afterwards is still false for anything that reaches send() in the same tick, and
      // the preamble goes out twice. The catch below puts the debt back.
      if (carry) scopeSentRef.current = true
      try {
        await client.send({
          sessionKey: sessionRef.current,
          // `message`, not `text` — chat.send reads params.message and rejects an empty one.
          message: carry && scope ? `${preamble(scope)}\n\n${body}` : body,
          ...(sending.length ? { attachments: sending } : {}),
        })
      } catch (e) {
        if (carry) scopeSentRef.current = false // it never reached the daemon; the retry carries it
        setRunning(false)
        setItems((prev) => [
          ...prev,
          { kind: 'bot', text: `**Could not send.** ${String((e as Error)?.message || e)}`, streaming: false },
        ])
      }
    },
    [client, pending, running],
  )

  const abort = useCallback(async () => {
    try {
      await client.abort(sessionRef.current)
    } catch {
      // the run may have just ended on its own — there is nothing to report to the user here
    }
  }, [client])

  // ── which conversation ────────────────────────────────────────────────────
  const reset = useCallback(() => {
    const key = newSessionKey()
    sessionRef.current = key
    scopeRef.current = null // a fresh chat is about nothing until told
    scopeSentRef.current = false
    setSessionKey(key)
    setItems([])
    setRunning(false)
    setPending([])
    callbacks.current.onSession?.(key)
  }, [])

  /** Open a saved conversation: render its transcript, then keep talking INTO it — the same
   *  sessionKey goes back out on the next send, so the thread continues rather than forking. */
  const open = useCallback(
    async (key: string) => {
      sessionRef.current = key
      scopeRef.current = null // a resumed chat already carries its context in message 1
      scopeSentRef.current = true
      setSessionKey(key)
      setItems([])
      setRunning(false)
      setPending([])
      callbacks.current.onSession?.(key)
      try {
        const res = await client.history(key, AGENT_ID)
        const replayed = (res?.messages || []).flatMap(replay)
        if (sessionRef.current === key) setItems(replayed)
      } catch (e) {
        if (sessionRef.current !== key) return
        setItems([
          {
            kind: 'bot',
            text: `**Could not load this chat.** ${String((e as Error)?.message || e)}`,
            streaming: false,
          },
        ])
      }
    },
    [client],
  )

  /** Point this conversation at an existing agent. The row is appended to the thread so the
   *  subject of the conversation is visible in it, not just in the chrome. */
  const setScope = useCallback((agent: AgentRow | null) => {
    scopeRef.current = agent
    scopeSentRef.current = false
    if (!agent) return
    setItems((prev) => [...prev, { kind: 'scope', id: agent.id, name: agent.name || agent.id }])
  }, [])

  return {
    items,
    running,
    pending,
    sessionKey,
    send,
    abort,
    reset,
    open,
    setScope,
    addFiles,
    removeFile,
  }
}

/** One stored message -> the same items a live run produces.
 *
 *  IN BLOCK ORDER. A transcript's `content` is already the sequence the model produced, so a
 *  reopened chat can look exactly like the live one did. The vanilla window rendered every
 *  tool_use first and the prose after, which regrouped a conversation on reload — the same text
 *  under a different story. */
function replay(m: any): ThreadItem[] {
  if (m?.role === 'user') {
    const text =
      typeof m.content === 'string'
        ? m.content
        : (m.content || []).map((c: any) => c?.text || '').join('')
    return text.trim() ? [{ kind: 'user', text, files: [] }] : []
  }
  if (m?.role !== 'assistant') return []

  const out: ThreadItem[] = []
  for (const [i, c] of (Array.isArray(m.content) ? m.content : []).entries()) {
    if (c?.type === 'text' && String(c.text || '').trim()) {
      out.push({ kind: 'bot', text: String(c.text), streaming: false })
    } else if (c?.type === 'tool_use' || c?.type === 'toolcall') {
      out.push({
        kind: 'tool',
        id: String(c.id ?? `${i}`),
        name: String(c.name || 'tool'),
        args: summarize(c.input || c.arguments),
        done: true,
        // The transcript does not record whether a past call failed, so it is shown as completed
        // rather than as a failure it may not have been.
        error: false,
      })
    }
  }
  return out
}
