/* The conversation, in the order it happened.
 *
 * AUTOSCROLL IS UNCONDITIONAL, matching agentd: every change to the item list pins the view to the
 * bottom. This window used to follow the user instead — it stopped the moment you scrolled away
 * and resumed when you came back — and that was dropped deliberately, so the two windows behave
 * the same way. The cost is real and known: scrolling up to re-read something during a live run
 * pulls you back down on the next token.
 *
 * Tool activity is shown as it happens — that is how you watch an agent being built.
 *
 * AN EMPTY CONVERSATION IS NOT THIS COMPONENT'S PROBLEM. The greeting, the start cards and the
 * centred composer are a layout of the whole screen rather than a state of the transcript, so App
 * owns that branch — the same split agentd makes in ChatView.
 */

import { useEffect, useRef, type ReactElement } from 'react'
import type { ThreadItem } from '../agentd/chat'
import { dayLabel, sameDay } from '../lib/timefmt'
import MessageItem from './MessageItem'
import { Thinking } from './Thinking'

export function Thread({ items, running }: { items: ThreadItem[]; running: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items, running])

  /* DATE SEPARATORS between calendar days, the same pass agentd makes.
   *
   * A conversation with this window routinely spans days of building, so "when was this" is a
   * real question about the scrollback rather than a decoration — and per-message times alone
   * cannot answer it, because a clock with no date reads as today.
   *
   * ONLY STAMPED ITEMS MOVE THE MARKER. `lastTs` is left alone for an item with no `ts` (a scope
   * or intent row, or anything restored from a transcript the daemon never timestamped), so an
   * unstamped item in the middle of a day cannot split it in two.
   */
  const rendered: ReactElement[] = []
  let lastTs: number | undefined
  items.forEach((item, i) => {
    if (item.ts && (!lastTs || !sameDay(item.ts, lastTs))) {
      rendered.push(
        <div key={`day-${i}`} className="msg-system">
          {dayLabel(item.ts)}
        </div>,
      )
    }
    if (item.ts) lastTs = item.ts
    rendered.push(<MessageItem key={i} item={item} running={running} />)
  })

  // Shown while the run has nothing to say YET — before the first token, and through every tool
  // call. Once prose is streaming the caret is already proof of life, and a second indicator under
  // it would just be noise.
  const last = items[items.length - 1]
  const streamingProse = last?.kind === 'bot' && last.streaming
  const working = running && !streamingProse

  return (
    <div className="thread" ref={boxRef}>
      <div className="thread-inner">
        {rendered}
        {working && <Thinking />}
      </div>
    </div>
  )
}
