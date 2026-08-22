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
/** Reasoning. Kept in the ordered list like everything else — it happened at a point in time and
 *  moving it out would lose that — but rendered in a box of its own rather than as transcript.
 *
 *  `streaming` is what tells the view to keep the box open and following; `seconds` is measured
 *  when it closes, so a finished block can collapse to "Thought for 34s". */
export interface ThinkItem {
  kind: 'think'
  text: string
  streaming: boolean
  startedAt: number
  seconds: number
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
/** The one decision taken before a new agent exists: does it get a window of its own?
 *
 *  In the thread, and not only in the chrome, because it is an instruction the model was given —
 *  the user needs to be able to see what it was told, in the place everything else it was told
 *  appears. */
export interface IntentItem {
  kind: 'intent'
  window: boolean
}

export type ThreadItem =
  | ScopeItem
  | IntentItem
  | UserItem
  | BotItem
  | ThinkItem
  | ToolItem
  | FallbackItem

/** What the user chose in the start dialog, for an agent that does not exist yet. */
export interface NewAgentIntent {
  window: boolean
}

/* ── the plan ───────────────────────────────────────────────────────────────────────────────
 *
 * `update_plan` carries the WHOLE plan every time it is called, not a delta. So the panel keeps
 * the LATEST call and replaces — appending would leave four copies of a growing list on screen
 * after a build that re-planned four times.
 *
 * It is deliberately not a thread item. A plan is the current state of the work, not something
 * that was said at a point in time, and the fourth copy of it scrolled past is worse than useless.
 */

export type PlanStatus = 'pending' | 'in_progress' | 'completed'

export interface PlanStep {
  step: string
  status: PlanStatus
  tool?: string
}

export interface Plan {
  explanation: string
  steps: PlanStep[]
}

export const PLAN_TOOL = 'update_plan'

/** The plan out of an `update_plan` argument object, or null if it is not one.
 *
 *  Every field is checked rather than trusted: these arguments are model output, and a malformed
 *  plan should render nothing rather than a panel full of `undefined`. */
function planFrom(args: unknown): Plan | null {
  if (!args || typeof args !== 'object') return null
  const raw = (args as Record<string, unknown>).plan
  if (!Array.isArray(raw)) return null
  const steps: PlanStep[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const step = String((item as Record<string, unknown>).step || '').trim()
    if (!step) continue
    const status = String((item as Record<string, unknown>).status || 'pending')
    steps.push({
      step,
      status: status === 'completed' || status === 'in_progress' ? status : 'pending',
      tool: String((item as Record<string, unknown>).tool || '') || undefined,
    })
  }
  if (!steps.length) return null
  return {
    explanation: String((args as Record<string, unknown>).explanation || '').trim(),
    steps,
  }
}

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

/** The window decision, as an instruction rather than a hint.
 *
 *  BLUNT ON PURPOSE, and blunter in the negative case. "Give it a window" is self-correcting — a
 *  missing window is the first thing the user notices. "Do not give it a window" is not: an agent
 *  builds one anyway, nobody sees a problem, and the user finds out when a folder they did not ask
 *  for turns up in the inspector. So the no-window text names the specific things not to do rather
 *  than describing a preference, and says whose decision it was. */
function intentPreamble(intent: NewAgentIntent): string {
  return intent.window
    ? '[context] We are creating a NEW agent, and the user has chosen that it HAS ITS OWN APP ' +
        'WINDOW. Declare `[app]` in its agent.toml and build the window as part of this work.'
    : '[context] We are creating a NEW agent, and the user has chosen that it has NO APP WINDOW. ' +
        'Do NOT declare `[app]` in its agent.toml, do NOT create a `ui/` or `app/` directory, and ' +
        'do NOT scaffold one. It is used from the agentd window, which is what the user asked ' +
        'for. If you believe it needs a window, say so and wait — do not build one anyway.'
}

const newSessionKey = () => `builder-${Date.now().toString(36)}`

/** Stamp a still-open thinking block as finished, unless more thinking is what is arriving.
 *
 *  Reasoning ends the moment ANYTHING else does — a token of the answer, a tool call, the end of
 *  the turn. Without this the box would keep growing and following for the rest of the run, which
 *  is the wall of text this whole treatment exists to end. */
function closeThinking(items: ThreadItem[], stillThinking: boolean): ThreadItem[] {
  const next = [...items]
  const last = next[next.length - 1]
  if (stillThinking || last?.kind !== 'think' || !last.streaming) return next
  next[next.length - 1] = {
    ...last,
    streaming: false,
    seconds: Math.max(1, Math.round((Date.now() - last.startedAt) / 1000)),
  }
  return next
}

export function useChat(
  client: AgentdClient,
  opts: { onToolDone?: () => void; onSession?: (key: string) => void } = {},
) {
  const [items, setItems] = useState<ThreadItem[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [running, setRunning] = useState(false)
  const [pending, setPending] = useState<Attachment[]>([])
  const [sessionKey, setSessionKey] = useState(newSessionKey)

  const sessionRef = useRef(sessionKey)
  sessionRef.current = sessionKey

  // The agent this conversation is ABOUT (null = we are creating something new). Carried into the
  // FIRST message only; after that it is in the transcript and repeating it is noise.
  const scopeRef = useRef<AgentRow | null>(null)
  const scopeSentRef = useRef(false)

  // The window decision for an agent that does not exist yet.
  //
  // CARRIED ON EVERY MESSAGE, unlike the scope above, until `create_agent` actually succeeds. A
  // scope preamble names something that already exists, so the model can re-read it from disk at
  // any point and one mention is enough. This names something that does not exist, so there is
  // nothing to re-read — it survives only as a sentence in the transcript, and a sentence twenty
  // messages up is a sentence a model will build straight past. "No window" is the direction that
  // fails silently: nobody notices the UI they did not ask for until it is built.
  //
  // Cleared the moment the agent exists, because from then on the answer is in its agent.toml —
  // repeating it would be both noise and a second source of truth.
  const intentRef = useRef<NewAgentIntent | null>(null)

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
        const next = closeThinking(prev, kind === 'think')
        const last = next[next.length - 1]
        if (kind === 'bot') {
          if (last?.kind === 'bot' && last.streaming) {
            next[next.length - 1] = { ...last, text: last.text + delta }
          } else {
            next.push({ kind: 'bot', text: delta, streaming: true })
          }
        } else if (last?.kind === 'think' && last.streaming) {
          next[next.length - 1] = { ...last, text: last.text + delta }
        } else {
          next.push({ kind: 'think', text: delta, streaming: true, startedAt: Date.now(), seconds: 0 })
        }
        return next
      })

    /** Commit whatever is streaming and drop the caret. Called whenever the assistant STOPS
     *  writing prose — a tool starting counts, and forgetting it left a blinking caret stranded
     *  on every bubble that was interrupted by a tool call.
     *
     *  It closes an open THINKING block too, which is what stamps its duration and lets the box
     *  fold down to one line. */
    const settle = () =>
      setItems((prev) =>
        closeThinking(prev, false).map((it, i, all) =>
          i === all.length - 1 && it.kind === 'bot' && it.streaming
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
          const name = String(ev.toolName || '?')
          // THE PLAN IS NOT A MESSAGE. It goes to the pinned panel and REPLACES what is there;
          // it never becomes a row, because every re-plan would leave another stale copy of a
          // growing list scrolled up the thread.
          if (name === PLAN_TOOL) {
            const next = planFrom(ev.args)
            if (next) setPlan(next)
            return
          }
          setItems((prev) => [
            ...prev,
            {
              kind: 'tool',
              id: String(ev.toolCallId || ev.toolName || Math.random()),
              name,
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
          // THE AGENT NOW EXISTS, so the window decision has been acted on and stops riding every
          // message — from here the answer is in its agent.toml. Keyed off SUCCESS: a create that
          // failed has decided nothing, and dropping the instruction there would let the retry go
          // out with no window decision at all.
          if (String(ev.toolName || '') === 'create_agent' && !ev.isError) intentRef.current = null
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
      const context = [
        carry && scope ? preamble(scope) : '',
        intentRef.current ? intentPreamble(intentRef.current) : '',
      ].filter(Boolean)
      try {
        await client.send({
          sessionKey: sessionRef.current,
          // `message`, not `text` — chat.send reads params.message and rejects an empty one.
          message: context.length ? `${context.join('\n')}\n\n${body}` : body,
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
    intentRef.current = null
    setSessionKey(key)
    setItems([])
    setPlan(null)
    setRunning(false)
    setPending([])
    callbacks.current.onSession?.(key)
  }, [])

  /** Open a saved conversation: render its transcript, then keep talking INTO it — the same
   *  sessionKey goes back out on the next send, so the thread continues rather than forking.
   *
   *  Returns the agent this conversation was ABOUT, if the transcript says. Reopening a chat used
   *  to blank the inspector: focus was cleared for every session change, and only a NEW chat has
   *  no subject. A resumed one does — it is written down in its own first message — so it is read
   *  back rather than thrown away. */
  const open = useCallback(
    async (key: string): Promise<string> => {
      sessionRef.current = key
      scopeRef.current = null // a resumed chat already carries its context in message 1
      scopeSentRef.current = true
      intentRef.current = null
      setSessionKey(key)
      setItems([])
      setPlan(null)
      setRunning(false)
      setPending([])
      callbacks.current.onSession?.(key)
      try {
        const res = await client.history(key, AGENT_ID)
        const messages = res?.messages || []
        const replayed = messages.flatMap(replay)
        if (sessionRef.current !== key) return ''
        setItems(replayed)
        // The plan a reopened conversation was left at — the LAST update_plan in the transcript,
        // for the same reason the live one replaces: every call carried the whole list.
        setPlan(lastPlanIn(messages))
        return subjectOf(messages)
      } catch (e) {
        if (sessionRef.current !== key) return ''
        setItems([
          {
            kind: 'bot',
            text: `**Could not load this chat.** ${String((e as Error)?.message || e)}`,
            streaming: false,
          },
        ])
        return ''
      }
    },
    [client],
  )

  /** Point this conversation at an existing agent. The row is appended to the thread so the
   *  subject of the conversation is visible in it, not just in the chrome. */
  const setScope = useCallback((agent: AgentRow | null) => {
    scopeRef.current = agent
    scopeSentRef.current = false
    intentRef.current = null // an existing agent already answered the window question
    if (!agent) return
    setItems((prev) => [...prev, { kind: 'scope', id: agent.id, name: agent.name || agent.id }])
  }, [])

  /** Declare that this conversation is building something NEW, and whether it gets a window. */
  const setIntent = useCallback((intent: NewAgentIntent) => {
    scopeRef.current = null
    scopeSentRef.current = false
    intentRef.current = intent
    setItems((prev) => [...prev, { kind: 'intent', window: intent.window }])
  }, [])

  return {
    items,
    plan,
    running,
    pending,
    sessionKey,
    send,
    abort,
    reset,
    open,
    setScope,
    setIntent,
    addFiles,
    removeFile,
  }
}

/** Which agent a saved conversation was about, read back out of the transcript.
 *
 *  Two witnesses, both written by this app itself and both durable:
 *
 *    the scope preamble  `We are working on the EXISTING agent \`x\`` — what `preamble()` sends
 *                        on the first message of a scoped chat, in plain sight in the transcript
 *    create_agent        the tool call that MADE it, whose `agent_id` argument names it
 *
 *  Nothing is inferred from prose. An unscoped chat that built nothing returns '' and the
 *  inspector stays empty, which is the honest answer. */
function subjectOf(messages: any[]): string {
  for (const m of messages) {
    const blocks = Array.isArray(m?.content) ? m.content : []
    if (m?.role === 'user') {
      const text =
        typeof m.content === 'string' ? m.content : blocks.map((c: any) => c?.text || '').join('')
      const found = /EXISTING agent `([^`]+)`/.exec(text)
      if (found) return found[1]
    }
    for (const c of blocks) {
      if ((c?.type === 'tool_use' || c?.type === 'toolcall') && c?.name === 'create_agent') {
        const id = (c.input || c.arguments || {})?.agent_id
        if (id) return String(id)
      }
    }
  }
  return ''
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
    } else if (c?.type === 'thinking' && String(c.thinking || c.text || '').trim()) {
      // Restored folded. The transcript does not record how long it took, so `seconds` stays 0
      // and the label says "Thought process" rather than inventing a duration.
      out.push({
        kind: 'think',
        text: String(c.thinking || c.text),
        streaming: false,
        startedAt: 0,
        seconds: 0,
      })
    } else if (c?.type === 'tool_use' || c?.type === 'toolcall') {
      // The plan belongs to the panel, live or restored — never to the thread.
      if (String(c.name || '') === PLAN_TOOL) continue
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

/** The plan a saved conversation was left at: the LAST `update_plan` anywhere in its transcript. */
function lastPlanIn(messages: any[]): Plan | null {
  let found: Plan | null = null
  for (const m of messages) {
    if (m?.role !== 'assistant' || !Array.isArray(m.content)) continue
    for (const c of m.content) {
      if ((c?.type !== 'tool_use' && c?.type !== 'toolcall') || String(c.name || '') !== PLAN_TOOL) {
        continue
      }
      found = planFrom(c.input || c.arguments) ?? found
    }
  }
  return found
}
