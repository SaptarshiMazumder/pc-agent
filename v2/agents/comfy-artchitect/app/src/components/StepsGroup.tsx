/* A run of the agent's tool calls, folded into one line the conversation can breathe around.
 *
 * WHY FOLD. A single render is a dozen calls — start the GPU, research, emit, validate, price,
 * install, run, download — and drawn one after another, open, they turned a conversation about a
 * picture into a build log. The person needs to know that work is happening and what it is doing
 * NOW; the full log is for when something looks wrong.
 *
 * NOTHING IS HIDDEN, only folded: the header carries the live step (the same progress line the
 * tool relays), an error marks the whole group, and opening it draws every call exactly as it
 * always was (MessageItem → ToolBlock, arguments and results intact).
 *
 * WHAT IS NEVER FOLDED: the ask (it is waiting on the person), the plan checklist, a milestone
 * ("Workflow saved", "Rendered 4 images") and a sub-agent — see `isFoldableStep`.
 */

import { AlertTriangle, Check, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { useState } from 'react'

import type { ThreadItem } from '../agentd/chat'
import MessageItem from './MessageItem'
import { milestoneFor } from './milestones'

type ToolItem = Extract<ThreadItem, { kind: 'tool' }>

/** A tool call that renders as a plain log row — the only kind this folds. */
export function isFoldableStep(item: ThreadItem): boolean {
  if (item.kind !== 'tool') return false
  if (item.name === 'ask_user' && item.done && !item.isError) return false
  if (item.name === 'update_plan' && Array.isArray((item.args as { plan?: unknown }).plan)) return false
  if (item.done && milestoneFor(item.name, item.args, item.result, item.isError)) return false
  return true
}

/** The words a tool is using right now, or the first line of what it answered. */
function latestLine(item: ToolItem): string {
  const live = (item.progress || '').split('\n').filter(Boolean).pop() || ''
  const first = (item.result || '').split('\n')[0] || ''
  return (item.done ? first : live).slice(0, 160)
}

export function StepsGroup({
  items,
  running,
  onSuggest,
  onDecide,
}: {
  items: ToolItem[]
  running: boolean
  onSuggest?: (prompt: string) => void
  onDecide?: (reply: string) => void
}) {
  const [open, setOpen] = useState(false)
  const busy = items.some((t) => !t.done)
  const failed = items.some((t) => t.isError)
  const last = items[items.length - 1]
  const n = items.length
  const label = busy ? 'Working' : `${n} step${n === 1 ? '' : 's'}`
  const line = latestLine(last)

  return (
    <div className={`steps${failed ? ' is-error' : ''}${busy ? ' is-busy' : ''}`}>
      <button type="button" className="steps-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="steps-ico" aria-hidden="true">
          {busy ? (
            <Loader2 size={14} className="spin" />
          ) : failed ? (
            <AlertTriangle size={14} />
          ) : (
            <Check size={14} strokeWidth={2.4} />
          )}
        </span>
        <span className="steps-label">{label}</span>
        <span className="steps-tool st-mono">{last.name}</span>
        {line && <span className="steps-line">{line}</span>}
        <span className="steps-caret" aria-hidden="true">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <div className="steps-body">
          {items.map((t, i) => (
            <MessageItem key={i} item={t} running={running} onSuggest={onSuggest} onDecide={onDecide} />
          ))}
        </div>
      )}
    </div>
  )
}

export default StepsGroup
