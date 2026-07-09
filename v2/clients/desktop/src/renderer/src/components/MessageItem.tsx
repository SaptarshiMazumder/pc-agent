import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Loader2, ChevronRight, ChevronDown, Sparkles, AlertTriangle, Copy, Pencil, Settings, Bot } from 'lucide-react'

import { timeLabel } from '../lib/timefmt'
import { useApp, type ChatItem } from '../state/store'
import ArtifactView from './ArtifactView'

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
    <button className={`msg-act ${copied ? 'ok' : ''}`} title={copied ? 'Copied' : 'Copy message'} onClick={copy}>
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  )
}

/** A user message bubble + its hover actions (Copy / Edit) — icons sharing ONE row with the
 *  timestamp. Edit loads the text back into the composer to tweak and re-send (store.seedComposer). */
function UserMessage({ item }: { item: Extract<ChatItem, { kind: 'user' }> }) {
  const seedComposer = useApp((s) => s.seedComposer)
  const stamp = item.ts ? timeLabel(item.ts) : ''
  return (
    <div className="msg-item">
      <div className="msg-user">
        {item.text && <div className="bubble">{item.text}</div>}
        {/* files the user attached (e.g. an edited image sent from the canvas) */}
        {item.artifacts?.length ? <ArtifactView artifacts={item.artifacts} /> : null}
        {(item.text || stamp) && (
          <div className="msg-meta">
            {item.text && (
              <div className="msg-actions">
                <CopyButton text={item.text} />
                <button className="msg-act" title="Edit message" onClick={() => seedComposer(item.text as string)}>
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
function AssistantMessage({ item }: { item: Extract<ChatItem, { kind: 'assistant' }> }) {
  const stamp = item.ts ? timeLabel(item.ts) : ''
  return (
    <div className="msg-item">
      <div className="msg-assistant markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
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

function ToolBlock({ item }: { item: Extract<ChatItem, { kind: 'tool' }> }) {
  const [expanded, setExpanded] = useState(true)
  const openToolConfig = useApp((s) => s.openToolConfig)
  const firstLine = (item.result.split('\n')[0] || '').slice(0, 160)
  // a still-running tool's incremental steps (e.g. the computer tool's "step 1: click …")
  const progress = item.progress || ''
  const lastStep = (progress.split('\n').filter(Boolean).pop() || '').slice(0, 160)
  return (
    <div className={`tool ${item.isError ? 'error' : ''}`}>
      <div className="tool-head" role="button" tabIndex={0} onClick={() => setExpanded((v) => !v)}>
        <span className={`tool-status ${item.done ? '' : 'spin'} ${item.isError ? 'err' : ''}`}>
          {/* a tool's icon is a gear; still a spinner while running / an alert on error */}
          {!item.done ? <Loader2 size={15} /> : item.isError ? <AlertTriangle size={15} /> : <Settings size={15} />}
        </span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-args">{summarizeArgs(item.args)}</span>
        <button className="tool-gear" title={`Configure ${item.name}`} onClick={(e) => { e.stopPropagation(); openToolConfig(item.name) }}>
          <Settings size={13} />
        </button>
        <span className="tool-caret">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </div>
      {item.done && firstLine && !expanded && <div className="tool-first">⎿ {firstLine}</div>}
      {item.done && expanded && <pre className="tool-full">{item.result || '(no output)'}</pre>}
      {/* while running: collapsed shows the latest step live, expanded shows the full step log */}
      {!item.done && !expanded && (lastStep
        ? <div className="tool-first live">⎿ {lastStep}</div>
        : <div className="tool-running">running…</div>)}
      {!item.done && expanded && (progress
        ? <pre className="tool-full">{progress}</pre>
        : <div className="tool-running">running…</div>)}
    </div>
  )
}

/** The update_plan tool — rendered with the SAME shell as every other tool call (bordered chip head,
 *  green gear icon, name, caret, open by default). Its BODY is a live checklist read straight from the
 *  call args (so it shows with no round-trip): ✓ done · ▶ in progress · ○ to do. */
function PlanBlock({ item }: { item: Extract<ChatItem, { kind: 'tool' }> }) {
  const [expanded, setExpanded] = useState(true)
  // only ANIMATE the in-progress spinner while the run is live — once it's done, a step left
  // in_progress shows a static marker instead of spinning forever
  const running = useApp((s) => s.sessions[s.currentSessionKey]?.running ?? false)
  const openToolConfig = useApp((s) => s.openToolConfig)
  const args = item.args as { plan?: unknown; explanation?: unknown }
  const plan = (Array.isArray(args.plan) ? args.plan : []) as Array<{ step?: string; status?: string }>
  const explanation = typeof args.explanation === 'string' ? args.explanation : ''
  const done = plan.filter((s) => s?.status === 'completed').length
  const summary = `${done}/${plan.length} done${explanation ? `  ·  ${explanation}` : ''}`
  return (
    <div className="tool">
      <div className="tool-head" role="button" tabIndex={0} onClick={() => setExpanded((v) => !v)}>
        <span className="tool-status"><Settings size={15} /></span>
        <span className="tool-name">update_plan</span>
        <span className="tool-args">{summary}</span>
        <button className="tool-gear" title="Configure update_plan" onClick={(e) => { e.stopPropagation(); openToolConfig('update_plan') }}>
          <Settings size={13} />
        </button>
        <span className="tool-caret">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </div>
      {expanded && (
        <ul className="todo-list">
          {plan.map((s, i) => {
            const status = String(s?.status || 'pending')
            return (
              <li key={i} className={`todo-step ${status}`}>
                <span className="todo-check">
                  {status === 'completed'
                    ? <Check size={13} />
                    : status === 'in_progress'
                      ? (running ? <Loader2 size={13} className="spin" /> : <span className="todo-dot active" />)
                      : <span className="todo-dot" />}
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

/** A delegated sub-agent run, rendered with the SAME shell as a tool call (bordered chip head,
 *  caret, open by default) but with a Bot icon + its own name — so it's clearly one block, not a
 *  scatter of centered system lines. The body is the child's beats ONE LEVEL DOWN: each tool it ran,
 *  the current one pulsing while it's live. */
function SubagentBlock({ item }: { item: Extract<ChatItem, { kind: 'subagent' }> }) {
  const [expanded, setExpanded] = useState(true)
  const { agent, steps, status, detail } = item
  const n = steps.length
  const summary =
    status === 'running' ? `${n} step${n === 1 ? '' : 's'} · running…`
    : status === 'error' ? `failed${detail ? ` · ${detail}` : ''}`
    : `${n} step${n === 1 ? '' : 's'} · done`
  return (
    <div className={`tool ${status === 'error' ? 'error' : ''}`}>
      <div className="tool-head" role="button" tabIndex={0} onClick={() => setExpanded((v) => !v)}>
        <span className={`tool-status ${status === 'running' ? 'spin' : ''} ${status === 'error' ? 'err' : ''}`}>
          {status === 'running' ? <Loader2 size={15} /> : status === 'error' ? <AlertTriangle size={15} /> : <Bot size={15} />}
        </span>
        <span className="tool-name">subagent · {agent}</span>
        <span className="tool-args">{summary}</span>
        <span className="tool-caret">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </div>
      {expanded && n > 0 && (
        <ul className="todo-list">
          {steps.map((s, i) => {
            const live = status === 'running' && i === n - 1
            return (
              <li key={i} className={`todo-step ${live ? 'in_progress' : ''}`}>
                <span className="todo-check">
                  {live ? <Loader2 size={13} className="spin" /> : <span className="todo-dot active" />}
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

export default function MessageItem({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case 'user':
      return <UserMessage item={item} />
    case 'assistant':
      return <AssistantMessage item={item} />
    case 'thinking':
      return (
        <div className="msg-item">
          <div className="msg-thinking">
            <div className="thinking-label"><Sparkles size={13} />thinking</div>
            <div className="thinking-text">{item.text}</div>
          </div>
        </div>
      )
    case 'tool':
      // update_plan renders as a Claude-style checklist panel (its plan lives in the call args, so
      // it shows live without a round-trip); every other tool stays a pure text log.
      if (item.name === 'update_plan' && Array.isArray((item.args as { plan?: unknown }).plan)) {
        return <div className="msg-item"><PlanBlock item={item} /></div>
      }
      return <div className="msg-item"><ToolBlock item={item} /></div>
    case 'subagent':
      return <div className="msg-item"><SubagentBlock item={item} /></div>
    case 'system':
      return <div className={`msg-system ${item.tone === 'error' ? 'error' : ''}`}>{item.text}</div>
    default:
      return null
  }
}
