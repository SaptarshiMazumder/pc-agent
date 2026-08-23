/* One item in the conversation — COPIED FROM agentd's MessageItem.tsx.
 *
 * WHAT CHANGED ON THE WAY OVER, and why each one:
 *
 *   ArtifactView      this window's own. agentd's offers View (its Canvas editor), Download and
 *                     Reveal (Electron's file bridge); a page served by the daemon has none of
 *                     those, so it renders the media and links the file instead.
 *   tool-config gear  agentd's tool rows carry a gear that opens that tool's settings page. There
 *                     is no such page in this window, so the button is absent rather than dead.
 *   `running` a prop  agentd reads it from its store. Here it is passed down instead, because
 *                     `running` belongs to a SESSION and this component does not know which one
 *                     it is rendering. Used for one thing: whether an in-progress step spins.
 *   scope / intent /  this window's own items, which agentd has no equivalent of. They keep their
 *   fallback           own rendering.
 */

import { useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  Pencil,
  Settings,
  Sparkles,
} from 'lucide-react'

import type { SubagentItem, ThreadItem, ToolItem, UserItem, BotItem } from '../agentd/chat'
import { timeLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import ArtifactView from './ArtifactView'
import Markdown from './Markdown'

/** Icon-only copy button with a brief "copied" flash. Shared by user + assistant messages. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = (): void => {
    if (!text) return
    void navigator.clipboard?.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }
  return (
    <button
      className={`msg-act ${copied ? 'ok' : ''}`}
      title={copied ? 'Copied' : 'Copy message'}
      onClick={copy}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  )
}

/** A user message bubble + its hover actions (Copy / Edit) — icons sharing ONE row with the
 *  timestamp. Edit loads the text back into the composer to tweak and re-send. */
function UserMessage({ item }: { item: UserItem & { ts?: number } }) {
  const seedComposer = useApp((s) => s.seedComposer)
  const stamp = item.ts ? timeLabel(item.ts) : ''
  return (
    <div className="msg-item">
      <div className="msg-user">
        {item.text && <div className="bubble">{item.text}</div>}
        {item.files.length > 0 && (
          // What the user attached, on their own bubble — an image as a thumbnail so they can see
          // WHICH screenshot they sent, anything else as a named chip.
          <div className="msg-files">
            {item.files.map((a, i) =>
              a.mimeType?.startsWith('image/') ? (
                <img
                  key={i}
                  src={`data:${a.mimeType};base64,${a.dataBase64}`}
                  alt={a.name}
                  title={a.name}
                />
              ) : (
                <span className="chip-file" key={i}>
                  {a.name}
                </span>
              ),
            )}
          </div>
        )}
        {(item.text || stamp) && (
          <div className="msg-meta">
            {item.text && (
              <div className="msg-actions">
                <CopyButton text={item.text} />
                <button
                  className="msg-act"
                  title="Edit message"
                  onClick={() => seedComposer(item.text)}
                >
                  <Pencil size={14} />
                </button>
              </div>
            )}
            {stamp && <div className="msg-time">{stamp}</div>}
          </div>
        )}
      </div>
    </div>
  )
}

/** An assistant answer + a Copy action sharing ONE row with the response time (once done). */
function AssistantMessage({ item }: { item: BotItem & { ts?: number } }) {
  const stamp = item.ts ? timeLabel(item.ts) : ''
  return (
    <div className="msg-item">
      <div className="msg-assistant markdown">
        <Markdown text={item.text} />
        {item.streaming && <span className="caret" />}
      </div>
      <ArtifactView artifacts={item.artifacts} />
      {!item.streaming && (item.text || stamp) && (
        <div className="msg-meta">
          {item.text && (
            <div className="msg-actions">
              <CopyButton text={item.text} />
            </div>
          )}
          {stamp && <div className="msg-time">{stamp}</div>}
        </div>
      )}
    </div>
  )
}

/** A one-line preview of a value for the tool-call summary. Objects/arrays are JSON-stringified —
 *  `String({})`/`String([{}])` yields the useless "[object Object]", so never coerce them. */
function previewValue(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v)
    } catch {
      return ''
    }
  }
  return String(v)
}

function summarizeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      const t = previewValue(v).replace(/\s+/g, ' ').trim()
      return `${k}=${t.length > 60 ? t.slice(0, 60) + '…' : t}`
    })
    .join('  ')
}

function ToolBlock({ item }: { item: ToolItem }) {
  const [expanded, setExpanded] = useState(true)
  const firstLine = (item.result.split('\n')[0] || '').slice(0, 160)
  // a still-running tool's incremental steps, from tool_progress
  const progress = item.progress || ''
  const lastStep = (progress.split('\n').filter(Boolean).pop() || '').slice(0, 160)
  return (
    <div className={`tool ${item.isError ? 'error' : ''}`}>
      <div className="tool-head" role="button" tabIndex={0} onClick={() => setExpanded((v) => !v)}>
        <span className={`tool-status ${item.done ? '' : 'spin'} ${item.isError ? 'err' : ''}`}>
          {/* a tool's icon is a gear; still a spinner while running / an alert on error */}
          {!item.done ? (
            <Loader2 size={15} />
          ) : item.isError ? (
            <AlertTriangle size={15} />
          ) : (
            <Settings size={15} />
          )}
        </span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-args">{summarizeArgs(item.args)}</span>
        <span className="tool-caret">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>
      {item.done && firstLine && !expanded && <div className="tool-first">⎿ {firstLine}</div>}
      {item.done && expanded && <pre className="tool-full">{item.result || '(no output)'}</pre>}
      {/* while running: collapsed shows the latest step live, expanded shows the full step log */}
      {!item.done &&
        !expanded &&
        (lastStep ? (
          <div className="tool-first live">⎿ {lastStep}</div>
        ) : (
          <div className="tool-running">running…</div>
        ))}
      {!item.done &&
        expanded &&
        (progress ? (
          <pre className="tool-full">{progress}</pre>
        ) : (
          <div className="tool-running">running…</div>
        ))}
    </div>
  )
}

/** The update_plan tool — the SAME shell as every other tool call (bordered chip head, gear icon,
 *  name, caret, open by default). Its BODY is a live checklist read straight from the call args,
 *  so it shows with no round-trip: ✓ done · ▶ in progress · ○ to do.
 *
 *  THIS WINDOW USED TO PIN IT ABOVE THE COMPOSER instead, replacing it on each re-plan, so a build
 *  that re-planned four times left one panel rather than four checklists up the thread. Rendering
 *  it inline is agentd's behaviour and brings agentd's consequence with it. */
function PlanBlock({ item, running }: { item: ToolItem; running: boolean }) {
  const [expanded, setExpanded] = useState(true)
  const args = item.args as { plan?: unknown; explanation?: unknown }
  const plan = (Array.isArray(args.plan) ? args.plan : []) as Array<{
    step?: string
    status?: string
  }>
  const explanation = typeof args.explanation === 'string' ? args.explanation : ''
  const done = plan.filter((s) => s?.status === 'completed').length
  const summary = `${done}/${plan.length} done${explanation ? `  ·  ${explanation}` : ''}`
  return (
    <div className="tool">
      <div className="tool-head" role="button" tabIndex={0} onClick={() => setExpanded((v) => !v)}>
        <span className="tool-status">
          <Settings size={15} />
        </span>
        <span className="tool-name">update_plan</span>
        <span className="tool-args">{summary}</span>
        <span className="tool-caret">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>
      {expanded && (
        <ul className="todo-list">
          {plan.map((s, i) => {
            const status = String(s?.status || 'pending')
            return (
              <li key={i} className={`todo-step ${status}`}>
                <span className="todo-check">
                  {status === 'completed' ? (
                    <Check size={13} />
                  ) : status === 'in_progress' ? (
                    // only ANIMATE while the run is live — a step left in_progress after the run
                    // ended shows a static marker instead of spinning forever
                    running ? (
                      <Loader2 size={13} className="spin" />
                    ) : (
                      <span className="todo-dot active" />
                    )
                  ) : (
                    <span className="todo-dot" />
                  )}
                </span>
                <span className="todo-text">{String(s?.step || '')}</span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** A delegated sub-agent run, with the SAME shell as a tool call but a Bot icon and its own name —
 *  so it reads as one block rather than a scatter of lines. The body is the child's beats ONE
 *  LEVEL DOWN: each tool it ran, the current one pulsing while it is live. */
function SubagentBlock({ item }: { item: SubagentItem }) {
  const [expanded, setExpanded] = useState(true)
  const { agent, steps, status, detail } = item
  const n = steps.length
  const summary =
    status === 'running'
      ? `${n} step${n === 1 ? '' : 's'} · running…`
      : status === 'error'
        ? `failed${detail ? ` · ${detail}` : ''}`
        : `${n} step${n === 1 ? '' : 's'} · done`
  return (
    <div className={`tool ${status === 'error' ? 'error' : ''}`}>
      <div className="tool-head" role="button" tabIndex={0} onClick={() => setExpanded((v) => !v)}>
        <span
          className={`tool-status ${status === 'running' ? 'spin' : ''} ${status === 'error' ? 'err' : ''}`}
        >
          {status === 'running' ? (
            <Loader2 size={15} />
          ) : status === 'error' ? (
            <AlertTriangle size={15} />
          ) : (
            <Bot size={15} />
          )}
        </span>
        <span className="tool-name">subagent · {agent}</span>
        <span className="tool-args">{summary}</span>
        <span className="tool-caret">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>
      {expanded && n > 0 && (
        <ul className="todo-list">
          {steps.map((s, i) => {
            const live = status === 'running' && i === n - 1
            return (
              <li key={i} className={`todo-step ${live ? 'in_progress' : ''}`}>
                <span className="todo-check">
                  {live ? (
                    <Loader2 size={13} className="spin" />
                  ) : (
                    <span className="todo-dot active" />
                  )}
                </span>
                <span className="todo-text">{s}</span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export default function MessageItem({ item, running }: { item: ThreadItem; running: boolean }) {
  switch (item.kind) {
    case 'user':
      return <UserMessage item={item} />
    case 'bot':
      return <AssistantMessage item={item} />
    case 'think':
      /* agentd's reasoning block: a quiet accent-ruled aside, always visible, no cap and no fold.
         THIS WINDOW USED TO CONTAIN IT — a fixed-height box that scrolled itself while streaming
         and folded to "Thought for 34s" when it finished, so minutes of reasoning could not bury
         the answer. That was dropped for agentd's, deliberately. Reasoning now grows the thread
         for as long as it runs. */
      return (
        <div className="msg-item">
          <div className="msg-thinking">
            <div className="thinking-label">
              <Sparkles size={13} />
              thinking
            </div>
            <div className="thinking-text">{item.text}</div>
          </div>
        </div>
      )
    case 'tool':
      // update_plan renders as a checklist panel (its plan lives in the call args, so it shows
      // live without a round-trip); every other tool stays a text log.
      if (item.name === 'update_plan' && Array.isArray((item.args as { plan?: unknown }).plan)) {
        return (
          <div className="msg-item">
            <PlanBlock item={item} running={running} />
          </div>
        )
      }
      return (
        <div className="msg-item">
          <ToolBlock item={item} />
        </div>
      )
    case 'subagent':
      return (
        <div className="msg-item">
          <SubagentBlock item={item} />
        </div>
      )

    /* ---- this window's own items ------------------------------------------------------- */
    case 'intent':
      // Shown for the same reason the scope row is: this is an instruction the model was given,
      // and a client that quietly prepends instructions to your words leaves you unable to tell
      // what it was actually asked.
      return (
        <div className="scope-row">
          <span className="scope-dot" />
          <span>
            Building a new agent <b>{item.window ? 'with its own window' : 'with no window'}</b>
          </span>
          <span className="scope-path">{item.window ? 'declares [app]' : 'runs in agentd'}</span>
        </div>
      )
    case 'scope':
      return (
        <div className="scope-row">
          <span className="scope-dot" />
          <span>
            Working on <b>{item.name}</b>
          </span>
          <span className="scope-path">agents/{item.id}/</span>
        </div>
      )
    case 'fallback':
      return (
        <div className="tool error">
          <div className="tool-head">
            <span className="tool-status err">
              <AlertTriangle size={15} />
            </span>
            <span className="tool-name">{item.from} unavailable</span>
            <span className="tool-args">
              → {item.to} · {item.reason}
            </span>
          </div>
        </div>
      )
    default:
      return null
  }
}
