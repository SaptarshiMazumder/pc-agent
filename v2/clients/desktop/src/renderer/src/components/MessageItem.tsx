import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Check, Loader2, ChevronRight, ChevronDown, Sparkles } from 'lucide-react'

import { timeLabel } from '../lib/timefmt'
import type { ChatItem } from '../state/store'

function summarizeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      const t = String(v ?? '').replace(/\n/g, ' ')
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
        <span className={`tool-status ${item.done ? '' : 'spin'}`} style={item.isError ? { color: 'var(--danger)' } : undefined}>
          {item.done ? <Check size={15} /> : <Loader2 size={15} />}
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
            <div className="bubble">
              {item.text}
              {stamp && <div className="msg-time">{stamp}</div>}
            </div>
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
      return <div className="msg-item"><ToolBlock item={item} /></div>
    case 'system':
      return <div className={`msg-system ${item.tone === 'error' ? 'error' : ''}`}>{item.text}</div>
    default:
      return null
  }
}
