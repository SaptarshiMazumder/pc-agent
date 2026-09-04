/* A run event arrives -> where it belongs in the transcript.
 *
 * LIFTED VERBATIM from the assistant's own window, because none of it is about what any
 * particular agent does. A `tool_execution_end` means the same thing everywhere, and two copies
 * of that reading would drift.
 *
 * ROUTED BY SESSION KEY. This is why more than one conversation can exist at once: every frame
 * finds its own conversation rather than the one on screen. An earlier version opened with
 * `if (payload.sessionKey !== current) return`, and the cost was that switching away from a
 * running chat binned its deltas — you came back to a stale transcript and an idle composer while
 * the daemon was still working.
 *
 * THE PAYLOAD IS NESTED. `{sessionKey, runId, agentId, ts, event}` — the type is `payload.event
 * .type`, one level down. Switching on `payload.type` misses every branch and the window silently
 * never updates, which is the single most common way an agent app is broken.
 */

import { closeThinking, resultText, type SubagentItem, type ThreadItem } from './chat'
import { freshArtifacts, readArtifacts, type Artifact } from './artifacts'
import { useApp, type AppState, type ChatSession } from '../state/store'

type Setter = (fn: (s: AppState) => Partial<AppState>) => void

/** Merge fields into ONE conversation. A key that no longer exists is not an error: a closed
 *  conversation can still receive frames from a run that was already in flight. */
function patch(
  set: Setter,
  key: string,
  fn: (session: ChatSession) => Partial<ChatSession>,
): void {
  set((s) => {
    const session = s.sessions[key]
    if (!session) return {}
    return { sessions: { ...s.sessions, [key]: { ...session, ...fn(session) } } }
  })
}

/** Merge fields into ONE conversation. A key that no longer exists is not an error: a closed
 *  conversation can still receive frames from a run that was already in flight. */

/** Fold one frame into the conversation it names. Wire it with
 *  `client.on('chat.event', (p) => handleRunEvent(p))`. */
export function handleRunEvent(payload: any): void {
  const set = useApp.setState as unknown as Setter
  const get = useApp.getState
  fold(set, get, payload)
}

/** Append to the bubble being streamed into, or start a new one.
 *
 *  A tool row (or anything else) landing in between ends the run of deltas, so the next one opens
 *  a fresh bubble BELOW it. That is the whole ordering rule. */
function appendTo(items: ThreadItem[], kind: 'bot' | 'think', delta: string, ts: number): ThreadItem[] {
  const next = closeThinking(items, kind === 'think')
  const last = next[next.length - 1]
  if (kind === 'bot') {
    if (last?.kind === 'bot' && last.streaming) {
      next[next.length - 1] = { ...last, text: last.text + delta }
    } else {
      next.push({ kind: 'bot', text: delta, streaming: true, ts })
    }
  } else if (last?.kind === 'think' && last.streaming) {
    next[next.length - 1] = { ...last, text: last.text + delta }
  } else {
    next.push({ kind: 'think', text: delta, streaming: true, ts })
  }
  return next
}

/** Commit whatever is streaming and drop the caret. Called whenever the assistant STOPS writing
 *  prose — a tool starting counts, and forgetting it left a blinking caret stranded on every
 *  bubble a tool call interrupted. It closes an open thinking block too. */
function settle(items: ThreadItem[]): ThreadItem[] {
  return closeThinking(items, false).map((it, i, all) =>
    i === all.length - 1 && it.kind === 'bot' && it.streaming ? { ...it, streaming: false } : it,
  )
}

/** Hang this run's files under its last answer, walking back from the end. A run that produced
 *  files and no prose gets a bare answer to carry them — otherwise the deliverable would exist
 *  with nowhere on screen to appear. Deduped against whatever that answer already holds. */
function attachArtifacts(items: ThreadItem[], artifacts: Artifact[], ts: number): ThreadItem[] {
  if (!artifacts.length) return items
  const next = [...items]
  for (let i = next.length - 1; i >= 0; i--) {
    const it = next[i]
    if (it.kind !== 'bot') continue
    const add = freshArtifacts(it.artifacts || [], artifacts)
    if (add.length) next[i] = { ...it, artifacts: [...(it.artifacts || []), ...add] }
    return next
  }
  next.push({ kind: 'bot', text: '', streaming: false, ts, artifacts })
  return next
}

function fold(
  set: (fn: (s: AppState) => Partial<AppState>) => void,
  get: () => AppState,
  payload: any,
): void {
  const key = String(payload?.sessionKey || '')
  const ev = payload?.event
  if (!key || !ev || !get().sessions[key]) return

  /* THE SERVER'S CLOCK (`ts`, epoch SECONDS), so every client shows the same send time and it
     matches what the transcript stores. */
  const at = Number(payload?.ts)
  const ts = Number.isFinite(at) && at > 0 ? at * 1000 : Date.now()
  const on = (fn: (s: ChatSession) => Partial<ChatSession>) => patch(set, key, fn)

  switch (ev.type) {
    /* The daemon measures the context after every assistant message and says so. It is not
       decoration: a conversation that outgrows its model fails silently — the provider returns
       nothing, the retry re-sends, and the user sees "couldn't generate a response" twice with no
       cause on screen. */
    case 'context_usage': {
      on(() => ({
        usage: {
          used: Number(ev.used || 0),
          limit: Number(ev.limit || 0),
          pct: Number(ev.pct || 0),
          model: String(ev.model || ''),
          cached: Number(ev.cached || 0),
        },
      }))
      return
    }

    case 'message_update': {
      const kind = ev.kind === 'thinking_delta' ? 'think' : ev.kind === 'text_delta' ? 'bot' : null
      if (kind) on((s) => ({ items: appendTo(s.items, kind, String(ev.delta || ''), ts) }))
      return
    }

    /* A CHILD AGENT'S RUN, folded into one item. `start` opens it, `tool` appends a step,
       `done`/`error` closes it. Matched by child name against the still-running block, the way a
       tool row is matched by call id. */
    case 'subagent_event': {
      const agent = String(ev.childAgent || 'agent')
      const kind = String(ev.kind || '')
      const status = kind === 'error' ? 'error' : kind === 'done' ? 'done' : ('running' as const)
      on((s) => {
        const items = [...s.items]
        let at = -1
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'subagent' && it.agent === agent && it.status === 'running') {
            at = i
            break
          }
        }
        if (at < 0) {
          items.push({
            kind: 'subagent',
            agent,
            steps: kind === 'tool' && ev.tool ? [String(ev.tool)] : [],
            status,
            detail: ev.detail ? String(ev.detail) : undefined,
            ts,
          })
        } else {
          const prev = items[at] as SubagentItem
          items[at] = {
            ...prev,
            steps: kind === 'tool' && ev.tool ? [...prev.steps, String(ev.tool)] : prev.steps,
            status,
            detail: ev.detail ? String(ev.detail) : prev.detail,
          }
        }
        return { items }
      })
      return
    }

    /* What a long-running tool is doing WHILE it does it, rather than a spinner and nothing. */
    case 'tool_progress': {
      const id = String(ev.toolCallId || ev.toolName || '')
      const line = String(ev.message || ev.text || ev.detail || '').trim()
      if (!line) return
      on((s) => {
        const items = [...s.items]
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'tool' && it.id === id && !it.done) {
            items[i] = { ...it, progress: it.progress ? `${it.progress}\n${line}` : line }
            return { items }
          }
        }
        return {}
      })
      return
    }

    case 'tool_execution_start': {
      // The assistant stopped writing to start a tool. Settle the bubble (and lose the caret) —
      // the next delta opens a fresh one below the tool row.
      on((s) => ({
        items: [
          ...settle(s.items),
          {
            kind: 'tool',
            id: String(ev.toolCallId || ev.toolName || Math.random()),
            name: String(ev.toolName || '?'),
            // RAW. Flattening here is what made a tool row permanently unexpandable.
            args: (ev.args || {}) as Record<string, unknown>,
            result: '',
            done: false,
            isError: false,
            ts,
          },
        ],
      }))
      return
    }

    case 'tool_execution_end': {
      const id = String(ev.toolCallId || ev.toolName || '')
      on((s) => {
        const items = [...s.items]
        // Last match wins: a tool called twice in one turn has two rows with the same name, and
        // the running one is always the later.
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'tool' && it.id === id && !it.done) {
            items[i] = { ...it, done: true, isError: !!ev.isError, result: resultText(ev.result) }
            return { items }
          }
        }
        return {}
      })
      // What the tool produced, if anything. Deduped against what is already waiting.
      const made = readArtifacts(ev.artifacts)
      if (made.length) {
        on((s) => ({
          pendingArtifacts: [...s.pendingArtifacts, ...freshArtifacts(s.pendingArtifacts, made)],
        }))
      }
      return
    }

    /* A run is MANY turns — the model answers, calls a tool, answers again. `turn_end` fires after
       each one, so it must only settle the current bubble; treating it as the end would flip the
       composer back to idle while the run is still going. */
    case 'message_end':
    case 'turn_end': {
      on((s) => ({ items: settle(s.items) }))
      return
    }

    // The configured model could not answer and another one took over. Never silent: "the model
    // you chose is not the one replying" is the fact that turns an unpaid API key from a mystery
    // into a one-line fix.
    case 'model_fallback': {
      on((s) => ({
        items: [
          ...settle(s.items),
          {
            kind: 'fallback',
            from: String(ev.from || '?'),
            to: String(ev.to || '?'),
            reason: String(ev.reason || '').slice(0, 120),
            ts,
          },
        ],
      }))
      return
    }

    /* `agent_end` is the run terminal (stopReason, and `error` when it failed).
     *
     * A FAILURE IS A SYSTEM NOTE, not an answer — the same shape agentd gives it. This used to
     * push a `bot` item reading "**Run failed.** …", which drew the failure as though the agent
     * had said it: a bubble, with a copy button, in the transcript's own voice. It is a fact
     * about the run, so it reads like the other facts about the run. */
    case 'agent_end': {
      on((s) => {
        const items = ev.error
          ? [
              ...settle(s.items),
              { kind: 'system' as const, tone: 'error' as const, text: String(ev.error), ts },
            ]
          : settle(s.items)
        return {
          running: false,
          items: attachArtifacts(items, s.pendingArtifacts, ts),
          pendingArtifacts: [],
        }
      })
      return
    }

    case 'error': {
      on((s) => ({
        running: false,
        items: [
          ...s.items,
          { kind: 'system', tone: 'error', text: String(ev.message || 'the run failed'), ts },
        ],
      }))
      return
    }
  }
}
