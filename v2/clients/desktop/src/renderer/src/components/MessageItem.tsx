import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Loader2, ChevronRight, ChevronDown, Sparkles, AlertTriangle } from 'lucide-react'

import { timeLabel } from '../lib/timefmt'
import type { ChatItem } from '../state/store'
import ArtifactView from './ArtifactView'

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
      {!item.done && <div className="tool-running">running…</div>}
    </div>
  )
}

export default function MessageItem({ item }: { item: ChatItem }) {
  const stamp = item.ts ? timeLabel(item.ts) : ''
  switch (item.kind) {
    case 'user':
      return (
        <div className="msg-item">
          <div className="msg-user">
            {item.text && <div className="bubble">{item.text}</div>}
            {/* files the user attached (e.g. an edited image sent from the canvas) */}
            {item.artifacts?.length ? <ArtifactView artifacts={item.artifacts} /> : null}
            {/* timestamp now sits OUTSIDE the bubble, right-aligned under it */}
            {stamp && <div className="msg-time">{stamp}</div>}
          </div>
        </div>
      )
    case 'assistant':
      return (
        <div className="msg-item">
          <div className="msg-assistant markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
            {item.streaming && <span className="caret" />}
          </div>
          <ArtifactView artifacts={item.artifacts} />
          {/* agent response time — LEFT-aligned under the answer, once it's done */}
          {stamp && !item.streaming && <div className="msg-time msg-time--left">{stamp}</div>}
        </div>
      )
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
    default:
      return null
  }
}
