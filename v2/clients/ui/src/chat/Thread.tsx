/**
 * THE SCROLLBACK — the list of messages, with a date separator between calendar days.
 *
 * Small enough to look like it did not need extracting, and that is exactly why it did: this is
 * the one place that decides what a conversation LOOKS like as a sequence, and the agent-app
 * bundle renders the same sequence. Left inline in ChatView, an app would have re-derived
 * "separator, then message, then remember the day" — and the two would agree until the first time
 * one of them gained a divider, a grouping rule, or an unread marker.
 *
 * Everything around it stays with its client: the shell keeps its scroll container, hero state,
 * composer and tabs; the app keeps its own. Only the sequence is shared.
 */

import type { JSX } from 'react'

import { dayLabel, sameDay } from '../lib/timefmt'
import MessageItem from '../components/MessageItem'
import type { ChatItem } from './session'

export default function Thread({ items }: { items: ChatItem[] }): JSX.Element {
  const rendered: JSX.Element[] = []
  let lastTs: number | undefined
  items.forEach((item, i) => {
    if (item.ts && (!lastTs || !sameDay(item.ts, lastTs))) {
      rendered.push(<div key={`day-${i}`} className="msg-system">{dayLabel(item.ts)}</div>)
    }
    if (item.ts) lastTs = item.ts
    rendered.push(<MessageItem key={i} item={item} />)
  })
  return <div className="chat-col">{rendered}</div>
}
