import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Loader2, ChevronRight, ChevronDown, Sparkles, AlertTriangle, Copy, Pencil } from 'lucide-react'

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
  const [expanded, setExpanded] = useState(false)
  const firstLine = (item.result.split('\n')[0] || '').slice(0, 160)
  // a still-running tool's incremental steps (e.g. the computer tool's "step 1: click …")
  const progress = item.progress || ''
  const lastStep = (progress.split('\n').filter(Boolean).pop() || '').slice(0, 160)
  return (
    <div className={`tool ${item.isError ? 'error' : ''}`}>
      <button className="tool-head" onClick={() => setExpanded((v) => !v)}>
        <span className={`tool-status ${item.done ? '' : 'spin'} ${item.isError ? 'err' : ''}`}>
          {!item.done ? <Loader2 size={15} /> : item.isError ? <AlertTriangle size={15} /> : <Check size={15} />}
        </span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-args">{summarizeArgs(item.args)}</span>
        <span className="tool-caret">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </button>
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
      // tool blocks stay a pure text log (terminal-style) — deliverables produced by a
      // tool are rendered under the assistant's answer, not here
      return <div className="msg-item"><ToolBlock item={item} /></div>
    case 'system':
      return <div className={`msg-system ${item.tone === 'error' ? 'error' : ''}`}>{item.text}</div>
    case 'trace':
      // per-step model/token trail — which brain ran this loop step and how many tokens it used
      return (
        <div className="msg-item">
          <div className="msg-trace" title="which model ran this step and its token usage">
            <span className="trace-step">step {item.step}</span>
            <span className="trace-model">{item.model || '—'}</span>
            <span className="trace-toks">↑ {item.tokensIn.toLocaleString()} · ↓ {item.tokensOut.toLocaleString()} tok</span>
          </div>
        </div>
      )
    default:
      return null
  }
}
