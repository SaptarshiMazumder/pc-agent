/* The conversation, in the order it happened.
 *
 * AUTOSCROLL IS STICKY: it follows the bottom only while you are at the bottom. Scroll up and
 * the view stays where you put it; send a message (or scroll back down) and it resumes. This was
 * unconditional for a while, deliberately, to match agentd — and agentd changed too, by the same
 * decision: being yanked down on every token makes the scrollback unreadable during exactly the
 * runs long enough to want to re-read something.
 *
 * Tool activity is shown as it happens — that is how you watch an agent being built.
 *
 * AN EMPTY CONVERSATION IS NOT THIS COMPONENT'S PROBLEM. The greeting, the start cards and the
 * centred composer are a layout of the whole screen rather than a state of the transcript, so App
 * owns that branch — the same split agentd makes in ChatView.
 */

import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { ArrowDown } from 'lucide-react'
import type { ThreadItem } from '../agentd/chat'
import { dayLabel, sameDay } from '../lib/timefmt'
import MessageItem from './MessageItem'
import { Thinking } from './Thinking'

export function Thread({ items, running }: { items: ThreadItem[]; running: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)

  /* STICKY, NOT UNCONDITIONAL. Following the bottom is only right while the user is AT the
     bottom; the moment they scroll up they are reading, and yanking them back down on every
     token makes the scrollback unreadable during exactly the runs long enough to want it.

     `stick` is a ref, not state: it changes on every scroll event and nothing renders from it.
     No flag juggling to tell our own scrolls from the user's — a programmatic pin lands AT the
     bottom, so the next scroll event measures ~0 and stick stays true; only a human wheeling up
     grows the distance.

     SENDING RE-PINS. Your own message is the one thing you always want to see land, and it is
     also the natural "resume following" gesture — so the effect forces the pin when the newest
     item is the user's. */
  const stick = useRef(true)
  /* THE SAME FACT, RENDERABLE. `stick` is a ref because it changes on every scroll event; but a
     button that offers the way back has to APPEAR, and nothing can render from a ref. So the
     boolean is mirrored into state and written only when it flips — one render per direction
     change, not one per scroll tick. Without this there was no way back to the bottom at all
     except sending a message, which is a poor thing to require of someone who just wanted to
     re-read the top of a long build. */
  const [atBottom, setAtBottom] = useState(true)

  const pin = useCallback((smooth = false) => {
    const el = boxRef.current
    if (!el) return
    stick.current = true
    setAtBottom(true)
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }, [])

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const last = items[items.length - 1]
    if (last?.kind === 'user') {
      stick.current = true
      setAtBottom(true)
    }
    if (stick.current) el.scrollTop = el.scrollHeight
  }, [items, running])

  /* CONTENT THAT ARRIVES LATE MUST NOT STRAND THE VIEW. The effect above pins at the moment the
     items change — but an image, a video or a downloaded render finishes loading AFTER that, and
     its height appears with no state change to react to. The thread was then left mid-scroll,
     following nothing, which is the "it jumps and then stops following" bug. Watching the inner
     content's box catches every one of those growths, and re-pins only while the user is still
     meant to be following. */
  useEffect(() => {
    const inner = innerRef.current
    if (!inner || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      const el = boxRef.current
      if (el && stick.current) el.scrollTop = el.scrollHeight
    })
    ro.observe(inner)
    return () => ro.disconnect()
  }, [])

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
    <div className="thread-wrap">
      <div
        className="thread"
        ref={boxRef}
        onScroll={(e) => {
          const el = e.currentTarget
          // 48px of slack: "basically at the bottom" counts, or the last token's own reflow would
          // un-stick the view the user never scrolled.
          const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
          stick.current = bottom
          // Written only on a FLIP, so wheeling through a long transcript is not one render per
          // scroll event.
          setAtBottom((was) => (was === bottom ? was : bottom))
        }}
      >
        <div className="thread-inner" ref={innerRef}>
          {rendered}
          {working && <Thinking />}
        </div>
      </div>

      {/* Only while it has somewhere to go. A button that says "scroll to the bottom" when you
          are already there is a button that lies. `running` makes it the live-tail control too. */}
      {!atBottom && (
        <button
          className="thread-jump"
          onClick={() => pin(true)}
          title={running ? 'Follow the run' : 'Jump to the latest message'}
        >
          <ArrowDown size={15} strokeWidth={2.2} />
          <span>{running ? 'Following off · jump to live' : 'Jump to latest'}</span>
        </button>
      )}
    </div>
  )
}
