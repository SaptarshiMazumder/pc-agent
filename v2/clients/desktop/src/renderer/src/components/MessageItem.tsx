import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { timeLabel } from '../lib/timefmt'
import type { ChatItem } from '../state/store'

function summarizeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => {
      const text = String(value ?? '').replace(/\n/g, ' ')
      return `${key}=${text.length > 60 ? text.slice(0, 60) + '…' : text}`
    })
    .join('  ')
}

function ToolBlock({ item }: { item: Extract<ChatItem, { kind: 'tool' }> }) {
  const [expanded, setExpanded] = useState(false)
  const firstLine = (item.result.split('\n')[0] || '').slice(0, 160)
  return (
    <div className={`tool ${item.isError ? 'tool-error' : ''}`}>
      <button className="tool-head" onClick={() => setExpanded((value) => !value)}>
        <span className="tool-dot">{item.done ? '⏺' : '◌'}</span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-args">{summarizeArgs(item.args)}</span>
      </button>
      {item.done && firstLine && !expanded && <div className="tool-result">⎿ {firstLine}</div>}
      {item.done && expanded && <pre className="tool-full">{item.result || '(no output)'}</pre>}
      {!item.done && <div className="tool-result running">running…</div>}
    </div>
  )
}

export default function MessageItem({ item }: { item: ChatItem }) {
  const stamp = item.ts ? timeLabel(item.ts) : ''
  switch (item.kind) {
    case 'user':
      return (
        <div className="msg msg-user">
          <div className="bubble">
            {item.text}
            {stamp && <span className="msg-time">{stamp}</span>}
          </div>
        </div>
      )
    case 'assistant':
      return (
        <div className="msg msg-assistant markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
          {item.streaming && <span className="caret" />}
          {!item.streaming && stamp && <div className="msg-time msg-time-block">{stamp}</div>}
        </div>
      )
    case 'thinking':
      return <div className="msg msg-thinking">{item.text}</div>
    case 'tool':
      return <ToolBlock item={item} />
    case 'system':
      return (
        <div className={`msg msg-system ${item.tone === 'error' ? 'msg-error' : ''}`}>
          {item.text}
          {stamp && <span className="msg-time"> {stamp}</span>}
        </div>
      )
    default:
      return null
  }
}
