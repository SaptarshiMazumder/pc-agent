/**
 * THE CONVERSATION, as pure data — one transcript model for every client on this platform.
 *
 * The shell's store owned all of this: the ChatItem shape, the replay of a saved transcript, and
 * the reducer that turns a run's events into rendered blocks. That was fine while the shell was
 * the only thing rendering a chat. It is not: an agent app renders the same conversation, from
 * the same events, over the SDK — and a second implementation of this file is a second set of
 * answers to "what does a tool block look like while it is running", which drift the moment
 * either side gains a feature.
 *
 * So the logic moved HERE, unchanged, and both sides call it:
 *
 *   the shell     state/store.ts  -> patchSession(key, s => reduceEvent(s, event, ts))
 *   an agent app  @agentd/canvas mountChat -> the same call, over its own socket
 *
 * PURE BY CONSTRUCTION. No store, no socket, no timers, no DOM: a session in, a session out.
 * What is NOT here is everything that is a property of the SHELL rather than of a conversation —
 * latency reporting, desktop notifications, the signed-out re-check on an auth-shaped failure.
 * Those stayed in the store, which is where the difference between the two clients belongs.
 */

import type { AgentEvent } from '../gateway/protocol'
import { resultText } from '../gateway/protocol'
import type { Artifact } from '../lib/artifacts'

export type ChatItem = (
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming: boolean }
  | { kind: 'thinking'; text: string; streaming: boolean }
  | { kind: 'tool'; name: string; args: Record<string, unknown>; result: string; isError: boolean; done: boolean; progress?: string }
  | { kind: 'system'; text: string; tone: 'info' | 'error' }
  // a delegated sub-agent run, GROUPED into one drillable block: the child's beats (each tool it
  // ran, one level down) accumulate under `steps` while it runs, then it settles done/error
  | { kind: 'subagent'; agent: string; steps: string[]; status: 'running' | 'done' | 'error'; detail?: string }
) & { ts?: number; artifacts?: Artifact[] } // ts: epoch ms (server-side); artifacts: media the item produced

export interface SessionState {
  items: ChatItem[]
  running: boolean
  // deliverables a tool produced, held until the next assistant answer renders them
  // (so the tool log stays pure text and media collects under the answer)
  pendingArtifacts?: Artifact[]
  // live model/token usage for the CURRENT (or most recent) loop step — surfaced in the
  // persistent status strip under the composer, Claude-style, NOT archived per step in the
  // scrollback. Populated from model_trace events (config.model_trace / AGENTD_MODEL_TRACE);
  // undefined when tracing is off.
  usage?: { model: string; tokensIn: number; tokensOut: number; step: number }
}

/** An empty conversation — the shape every reader can rely on before anything has arrived. */
export function emptySession(): SessionState {
  return { items: [], running: false }
}

/** ISO timestamp (as stored in the transcript) -> epoch ms, or undefined. */
export function toMs(iso: unknown): number | undefined {
  if (typeof iso !== 'string' || !iso) return undefined
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? undefined : ms
}

/** Incoming artifacts not already waiting in the pending buffer — dedupes WITHIN a run
 *  (so the model declaring the same file twice shows it once) while still allowing a
 *  later turn to re-present a file the user asks to see again. */
export function newArtifacts(session: SessionState, incoming?: Artifact[]): Artifact[] | undefined {
  if (!incoming?.length) return undefined
  const seen = new Set((session.pendingArtifacts || []).map((a) => a.path))
  const fresh = incoming.filter((a) => !seen.has(a.path))
  return fresh.length ? fresh : undefined
}

/** Attach deliverables to the last assistant bubble (walking from the end); if there is
 *  no assistant item yet, push a bare one to carry them. Dedupes against what is there. */
export function attachToLastAssistant(items: ChatItem[], artifacts: Artifact[], ts: number): void {
  if (!artifacts.length) return
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'assistant') {
      const have = new Set((items[i].artifacts || []).map((a) => a.path))
      const add = artifacts.filter((a) => !have.has(a.path))
      if (add.length) items[i] = { ...items[i], artifacts: [...(items[i].artifacts || []), ...add] }
      return
    }
  }
  items.push({ kind: 'assistant', text: '', streaming: false, ts, artifacts })
}

/**
 * Rebuild a saved transcript (sessions.history message dicts) into the same ChatItem[] the live
 * event path produces, so a resumed session renders identically: user text, assistant
 * text/thinking, and tool calls merged with their results. Each stored line carries `ts` — kept
 * so history shows real send times.
 */
export function historyToItems(messages: any[]): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndexByCallId = new Map<string, number>()
  // a "run" = a user message and everything until the next one. Tool deliverables buffer
  // for the whole run and attach to that run's FINAL assistant answer — matching the live
  // path (flush at agent_end), so multi-turn / repeated answers do not strand the media.
  let pending: Artifact[] = []
  let runLastTextIdx = -1

  const flushRun = () => {
    if (pending.length) {
      if (runLastTextIdx >= 0) {
        const have = new Set((items[runLastTextIdx].artifacts || []).map((a) => a.path))
        const add = pending.filter((a) => !have.has(a.path))
        if (add.length) {
          items[runLastTextIdx] = {
            ...items[runLastTextIdx],
            artifacts: [...(items[runLastTextIdx].artifacts || []), ...add]
          }
        }
      } else {
        attachToLastAssistant(items, pending, items[items.length - 1]?.ts ?? 0)
      }
    }
    pending = []
    runLastTextIdx = -1
  }

  for (const message of messages) {
    const ts = toMs(message.ts)
    if (message.role === 'user') {
      flushRun() // the previous run ended
      const atts = (message.attachments || []) as Artifact[] // files the user attached (by ref)
      items.push({ kind: 'user', text: String(message.content ?? ''), ts, ...(atts.length ? { artifacts: atts } : {}) })
    } else if (message.role === 'assistant') {
      for (const block of message.content || []) {
        if (block.type === 'text' && block.text) {
          runLastTextIdx = items.length // track the run's latest answer bubble
          items.push({ kind: 'assistant', text: block.text, streaming: false, ts })
        } else if (block.type === 'thinking' && block.thinking) {
          items.push({ kind: 'thinking', text: block.thinking, streaming: false, ts })
        } else if (block.type === 'toolCall') {
          toolIndexByCallId.set(String(block.id), items.length)
          items.push({ kind: 'tool', name: block.name || '?', args: block.arguments || {},
                       result: '', isError: false, done: false, ts })
        }
      }
      if (message.errorMessage) {
        items.push({ kind: 'system', tone: 'error', text: String(message.errorMessage), ts })
      }
    } else if (message.role === 'toolResult') {
      const text = (message.content || []).map((block: any) => block?.text || '').join('')
      // tool block is text-only; buffer its DECLARED deliverables for the run's answer
      for (const a of message.artifacts || []) {
        if (!pending.some((p) => p.path === a.path)) pending.push(a)
      }
      const index = toolIndexByCallId.get(String(message.toolCallId))
      if (index !== undefined && items[index]?.kind === 'tool') {
        const tool = items[index] as Extract<ChatItem, { kind: 'tool' }>
        items[index] = { ...tool, result: text, isError: !!message.isError, done: true }
      } else {
        // orphan result (no matching call in this transcript) — show it anyway
        items.push({ kind: 'tool', name: message.toolName || '?', args: {}, result: text, isError: !!message.isError, done: true, ts })
      }
    }
  }
  flushRun() // the final run
  return items
}

/** Streamed text: extend the open bubble of the same kind, or open one. */
export function appendStreaming(
  session: SessionState,
  kind: 'assistant' | 'thinking',
  delta: string,
  ts: number
): SessionState {
  const items = [...session.items]
  const last = items[items.length - 1]
  if (last && last.kind === kind && last.streaming) {
    items[items.length - 1] = { ...last, text: last.text + delta }
  } else {
    items.push({ kind, text: delta, streaming: true, ts } as ChatItem)
  }
  return { ...session, items }
}

/** Every streaming bubble settles. Used by both message_end and a clean agent_end. */
function settle(items: ChatItem[]): ChatItem[] {
  return items.map((item) => ('streaming' in item && item.streaming ? { ...item, streaming: false } : item))
}

/**
 * ONE run event -> the next conversation state.
 *
 * `agent_end` is the only case a caller must also look at itself, and only for what happens
 * AROUND a conversation rather than inside it (notifying, reporting, re-checking a rejected
 * credential). The transcript itself — the error line, the settling, the deliverable flush,
 * `running: false` — is finished here.
 */
export function reduceEvent(session: SessionState, event: AgentEvent, ts: number): SessionState {
  switch (event.type) {
    case 'message_update':
      if (event.kind === 'text_delta') return appendStreaming(session, 'assistant', event.delta || '', ts)
      if (event.kind === 'thinking_delta') return appendStreaming(session, 'thinking', event.delta || '', ts)
      return session
    case 'message_end':
      // just end streaming here — declared deliverables stay buffered and attach to the
      // FINAL answer at agent_end (flushing per-turn latched them onto intermediate turns
      // when the model took several turns / repeated itself)
      return { ...session, items: settle(session.items) }
    case 'model_trace':
      // update the persistent status strip (model + token usage), NOT the scrollback — Claude
      // shows this live in a status bar and hides it once the step is over, never archiving a
      // per-step line in the transcript. Each trace REPLACES the last (in-place, one indicator).
      return {
        ...session,
        usage: {
          model: String(event.model || session.usage?.model || ''),
          tokensIn: Number(event.tokensIn || 0),
          tokensOut: Number(event.tokensOut || 0),
          step: Number(event.step || 0)
        }
      }
    case 'tool_execution_start':
      return {
        ...session,
        items: [
          ...session.items,
          { kind: 'tool', name: event.toolName || '?', args: event.args || {}, result: '', isError: false, done: false, ts }
        ]
      }
    case 'tool_progress': {
      // a running tool's incremental steps (the computer tool's per-step updates, GuardedTool
      // retries, …) — accumulate onto the matching not-yet-done tool block so they render live
      const items = [...session.items]
      const text = String(event.text || '').trim()
      if (text) {
        for (let i = items.length - 1; i >= 0; i--) {
          const item = items[i]
          if (item.kind === 'tool' && !item.done && item.name === (event.toolName || '?')) {
            items[i] = { ...item, progress: item.progress ? [item.progress, text].join('\n') : text }
            break
          }
        }
      }
      return { ...session, items }
    }
    case 'tool_execution_end': {
      const items = [...session.items]
      for (let i = items.length - 1; i >= 0; i--) {
        const item = items[i]
        if (item.kind === 'tool' && !item.done && item.name === (event.toolName || '?')) {
          // tool block is text-only; its deliverables buffer for the coming answer
          items[i] = { ...item, result: resultText(event.result), isError: !!event.isError, done: true }
          break
        }
      }
      const fresh = newArtifacts(session, event.artifacts)
      const pendingArtifacts = fresh ? [...(session.pendingArtifacts || []), ...fresh] : session.pendingArtifacts
      return { ...session, items, pendingArtifacts }
    }
    case 'subagent_event': {
      const agent = event.childAgent
      const items = [...session.items]
      // attach to the most recent STILL-RUNNING block for this child; otherwise open a new one
      let idx = -1
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (it.kind === 'subagent' && it.agent === agent && it.status === 'running') { idx = i; break }
      }
      if (event.kind === 'start' || idx < 0) {
        // a fresh delegation (or a stray beat before its start) -> new grouped block
        items.push({ kind: 'subagent', agent, steps: event.kind === 'tool' && event.tool ? [event.tool] : [], status: event.kind === 'error' ? 'error' : event.kind === 'done' ? 'done' : 'running', detail: event.detail, ts })
      } else {
        const prev = items[idx] as Extract<ChatItem, { kind: 'subagent' }>
        if (event.kind === 'tool' && event.tool) {
          items[idx] = { ...prev, steps: [...prev.steps, event.tool] }
        } else if (event.kind === 'done' || event.kind === 'error') {
          items[idx] = { ...prev, status: event.kind, detail: event.detail }
        }
      }
      return { ...session, items }
    }
    case 'model_fallback':
      // The configured model could not serve this turn and another one answered instead.
      // Goes in the SCROLLBACK, not the status strip: it is a fact about this specific
      // exchange ("you are not talking to the model you chose, and here is why"), and it
      // used to live only in a log file — which is how an unpaid API key looked like the
      // app hanging for days.
      return {
        ...session,
        items: [
          ...session.items,
          {
            kind: 'system' as const,
            tone: 'error' as const,
            text:
              `${String(event.from || 'the configured model')} is unavailable — ` +
              `${String(event.to || 'a fallback')} answered instead. ` +
              String(event.reason || ''),
            ts
          }
        ]
      }
    case 'agent_end': {
      const error = runError(event)
      const items = error
        ? [...session.items, { kind: 'system' as const, tone: 'error' as const, text: error, ts }]
        : settle(session.items)
      // flush the run's DECLARED deliverables onto its final answer (once, at run end)
      if (session.pendingArtifacts?.length) attachToLastAssistant(items, session.pendingArtifacts, ts)
      return { ...session, items, running: false, pendingArtifacts: [] }
    }
    default:
      return session
  }
}

/** The error text an `agent_end` carries, or '' — so a caller can act on a failed run without
 *  re-deriving the rule the reducer already applied. */
export function runError(event: AgentEvent): string {
  if (event.type !== 'agent_end') return ''
  return event.stopReason === 'error' ? String(event.error || 'run failed') : ''
}
