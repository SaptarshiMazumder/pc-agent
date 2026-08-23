/* The conversation PROTOCOL: the item shapes, the preambles, and how a stored transcript is read
 * back. The conversations themselves — what is in them, which one is open, what is streaming —
 * live in state/store.ts, because more than one can be open at a time now and a hook holding one
 * of anything cannot answer for several.
 *
 * The conversation with Agent Builder.
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

import type { Attachment } from '@agentd/client'
import { readArtifacts, type Artifact } from './artifacts'
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
  /** Files this turn produced. Attached when the run ends rather than as they are declared: a
   *  tool announces a file the moment it writes one, which is usually before the assistant has
   *  said anything for them to hang under. */
  artifacts?: Artifact[]
}
/** Reasoning. Kept in the ordered list like everything else — it happened at a point in time and
 *  moving it out would lose that.
 *
 *  `streaming` is the only state it carries. It used to time itself as well, so a finished block
 *  could fold to "Thought for 34s"; agentd renders reasoning plainly and never folds it, so there
 *  is nothing left that could read a duration. */
export interface ThinkItem {
  kind: 'think'
  text: string
  streaming: boolean
}
/** A tool call, and what it returned.
 *
 *  ARGS ARE STORED RAW. They used to be flattened to a one-line string the moment the event
 *  arrived, which threw away everything the summary did not pick — so a row could never later be
 *  expanded to show what the tool was actually called with. Summarising is a rendering decision
 *  and now happens at render time.
 *
 *  THE RESULT IS STORED AT ALL, which it was not. `tool_execution_end` carries what the tool
 *  returned and this hook dropped it on the floor, so the window could tell you a tool had run
 *  and never what came back — the single biggest thing you could not see in this chat.
 *
 *  `progress` is the incremental step log from `tool_progress` (app-facing, and previously
 *  unhandled here): what a long-running tool is doing WHILE it does it, rather than a spinner. */
export interface ToolItem {
  kind: 'tool'
  id: string
  name: string
  args: Record<string, unknown>
  result: string
  progress?: string
  done: boolean
  isError: boolean
}
/** A delegated sub-agent run, collapsed into ONE item rather than a scatter of lines.
 *
 *  The daemon synthesises `subagent_event` for the parent's view (start / tool / done / error) so
 *  a child's whole run reads as a single block here with its steps underneath. This window
 *  ignored those events entirely, so delegating simply looked like a pause. */
export interface SubagentItem {
  kind: 'subagent'
  agent: string
  steps: string[]
  status: 'running' | 'done' | 'error'
  detail?: string
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

/** Epoch ms, stamped on arrival. Carried by every item so the thread can group by calendar day
 *  and show a per-message time — neither of which it could do before. */
export interface Stamped {
  ts?: number
}

export type ThreadItem = (
  | ScopeItem
  | IntentItem
  | UserItem
  | BotItem
  | ThinkItem
  | ToolItem
  | SubagentItem
  | FallbackItem
) &
  Stamped

/** What the user chose in the start dialog, for an agent that does not exist yet. */
export interface NewAgentIntent {
  window: boolean
}

/** The full text of a tool result — a message dict of content blocks, a `{text}`, or a bare
 *  string, depending on the tool. COPIED FROM agentd's gateway/protocol.ts, because a tool result
 *  has exactly one correct reading and two of them would drift.
 *
 *  Never `String(result)`: a content-block array coerces to "[object Object]", which is what a
 *  user would then be shown as the answer their tool returned. */
export function resultText(result: any): string {
  if (result && typeof result === 'object') {
    const content = result.content
    if (Array.isArray(content)) {
      return content
        .map((block: any) => (block && typeof block === 'object' ? block.text || '' : ''))
        .join('')
        .trim()
    }
    return String(result.text || '').trim()
  }
  return String(result ?? '').trim()
}

/** The attachment cap. Exported so the composer can SAY it rather than only obey it. */
export const MAX_FILES = 10

/** A clipboard image usually has no usable filename ('' or no extension). The daemon then stores
 *  it as literally "attachment" — which, having no extension, is not classified as an image, so a
 *  vision model never receives it as one. Name it from its mime type. */
function attachmentName(f: File): string {
  if (f.name && f.name.includes('.')) return f.name
  const ext = (f.type.split('/')[1] || 'bin').split('+')[0].replace(/[^a-z0-9]/gi, '')
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  return `${f.name || 'pasted'}-${stamp}.${ext}`
}

export function readFile(f: File): Promise<Attachment> {
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
export function preamble(agent: AgentRow): string {
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
export function intentPreamble(intent: NewAgentIntent): string {
  return intent.window
    ? '[context] We are creating a NEW agent, and the user has chosen that it HAS ITS OWN APP ' +
        'WINDOW. Declare `[app]` in its agent.toml and build the window as part of this work.'
    : '[context] We are creating a NEW agent, and the user has chosen that it has NO APP WINDOW. ' +
        'Do NOT declare `[app]` in its agent.toml, do NOT create a `ui/` or `app/` directory, and ' +
        'do NOT scaffold one. It is used from the agentd window, which is what the user asked ' +
        'for. If you believe it needs a window, say so and wait — do not build one anyway.'
}

export const newSessionKey = () =>
  `builder-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`

/** Stamp a still-open thinking block as finished, unless more thinking is what is arriving.
 *
 *  Reasoning ends the moment ANYTHING else does — a token of the answer, a tool call, the end of
 *  the turn. Without this the box would keep growing and following for the rest of the run, which
 *  is the wall of text this whole treatment exists to end. */
export function closeThinking(items: ThreadItem[], stillThinking: boolean): ThreadItem[] {
  const next = [...items]
  const last = next[next.length - 1]
  if (stillThinking || last?.kind !== 'think' || !last.streaming) return next
  next[next.length - 1] = { ...last, streaming: false }
  return next
}

export function subjectOf(messages: any[]): string {
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
/** A saved conversation, rebuilt.
 *
 *  WHY THIS IS NOT `messages.flatMap(replay)` ANY MORE. A tool's OUTPUT is stored as its own
 *  message — role `toolResult`, carrying the `toolCallId` of the call it answers — so a per-message
 *  map can never attach it to anything: by the time the result is seen, the call is already a
 *  finished item somewhere behind it. That is why reopening a chat used to show every tool as a
 *  bare name with nothing under it, while the same tool in a live run showed its output. The
 *  results were on disk the whole time; nothing read them.
 *
 *  So this walks the messages keeping an index of call id -> position, and patches the result back
 *  onto its call. A result whose call is missing from the transcript is shown on its own rather
 *  than dropped — an orphan is still something that happened. */
export function restore(messages: any[]): ThreadItem[] {
  const out: ThreadItem[] = []
  const toolAt = new Map<string, number>()
  /** Artifacts declared since the last assistant message, waiting for one to hang under. */
  const carried: Artifact[] = []

  for (const m of messages) {
    if (m?.role === 'toolResult') {
      const parsed = Date.parse(String(m?.ts || ''))
      const ts = Number.isFinite(parsed) ? parsed : undefined
      const text = (Array.isArray(m.content) ? m.content : [])
        .map((b: any) => b?.text || '')
        .join('')
        .trim()
      // A stored tool result declares what it produced. Buffered exactly as a live one is, and
      // flushed onto the next assistant message below.
      carried.push(...readArtifacts(m.artifacts))
      const at = toolAt.get(String(m.toolCallId))
      const call = at === undefined ? undefined : out[at]
      if (at !== undefined && call && call.kind === 'tool') {
        out[at] = { ...call, result: text, isError: !!m.isError, done: true }
      } else {
        out.push({
          kind: 'tool',
          id: String(m.toolCallId || ''),
          name: String(m.toolName || '?'),
          args: {},
          result: text,
          isError: !!m.isError,
          done: true,
          ts,
        })
      }
      continue
    }
    for (const item of replay(m)) {
      if (item.kind === 'tool') toolAt.set(item.id, out.length)
      // The first answer after a tool declared files carries them, matching what a live run does
      // at `agent_end`.
      if (item.kind === 'bot' && carried.length) {
        out.push({ ...item, artifacts: [...carried] })
        carried.length = 0
        continue
      }
      out.push(item)
    }
  }
  return out
}

function replay(m: any): ThreadItem[] {
  // The daemon stamps each stored message with an ISO timestamp (local_store.load_session). A
  // resumed conversation therefore keeps its real times and day breaks rather than collapsing to
  // "all of it, just now" — which is what an unparsed or missing stamp would look like. Invalid
  // or absent leaves `ts` undefined, and the thread simply shows no time for that item.
  const at = Date.parse(String(m?.ts || ''))
  const ts = Number.isFinite(at) ? at : undefined

  if (m?.role === 'user') {
    const text =
      typeof m.content === 'string'
        ? m.content
        : (m.content || []).map((c: any) => c?.text || '').join('')
    return text.trim() ? [{ kind: 'user', text, files: [], ts }] : []
  }
  if (m?.role !== 'assistant') return []

  const out: ThreadItem[] = []
  for (const [i, c] of (Array.isArray(m.content) ? m.content : []).entries()) {
    if (c?.type === 'text' && String(c.text || '').trim()) {
      out.push({ kind: 'bot', text: String(c.text), streaming: false, ts })
    } else if (c?.type === 'thinking' && String(c.thinking || c.text || '').trim()) {
      out.push({ kind: 'think', text: String(c.thinking || c.text), streaming: false, ts })
    } else if (c?.type === 'tool_use' || c?.type === 'toolcall') {
      out.push({
        kind: 'tool',
        id: String(c.id ?? `${i}`),
        name: String(c.name || 'tool'),
        args: (c.input || c.arguments || {}) as Record<string, unknown>,
        // Filled in by `restore` when the matching `toolResult` message comes past.
        result: '',
        done: true,
        ts,
        // The transcript does not record whether a past call failed, so it is shown as completed
        // rather than as a failure it may not have been.
        isError: false,
      })
    }
  }
  return out
}
