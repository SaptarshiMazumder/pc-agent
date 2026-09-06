/* The split between the conversation and the dashboard, in the user's hands.
 *
 * The studio ships at a width chosen for the design, which is the right DEFAULT and the wrong
 * answer for everyone reading a long transcript. So the conversation column is draggable — wider
 * only, never narrower than it ships (see CHAT_MIN_PX) — and the dashboard, being `flex: 1` over
 * `auto-fit` grids, simply re-columns into whatever is left.
 *
 * POINTER EVENTS, not mouse: one code path covers trackpad, mouse and touch, and `setPointerCapture`
 * keeps the drag alive when the cursor outruns a 6px handle — which it always does.
 *
 * The ceiling is computed FROM THE LIVE CONTAINER on each move rather than stored, because the
 * window can be resized between drags and a remembered ceiling would be wrong the moment it is.
 */

import { useCallback, useRef } from 'react'

import { CHAT_MIN_PX, DASH_MIN_PX, useApp } from '../../state/store'

export function ChatResizer({ side }: { side: 'left' | 'right' }) {
  const chatWidth = useApp((s) => s.chatWidth)
  const setChatWidth = useApp((s) => s.setChatWidth)
  const start = useRef({ x: 0, w: 0, max: 0 })

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const cols = e.currentTarget.parentElement
      const room = cols?.getBoundingClientRect().width ?? 0
      start.current = {
        x: e.clientX,
        w: chatWidth,
        // Never let the drag starve the dashboard. If the window is too narrow to honour both
        // minimums, the chat simply cannot grow (max collapses to the current width).
        max: Math.max(chatWidth, room - DASH_MIN_PX),
      }
      e.currentTarget.setPointerCapture(e.pointerId)
    },
    [chatWidth],
  )

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
      // Dragging the handle AWAY from the chat widens it, whichever side the chat is on.
      const delta = side === 'left' ? e.clientX - start.current.x : start.current.x - e.clientX
      const next = Math.min(start.current.max, Math.max(CHAT_MIN_PX, start.current.w + delta))
      setChatWidth(next)
    },
    [side, setChatWidth],
  )

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }, [])

  return (
    <div
      className="st-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the conversation column"
      title="Drag to widen the conversation · double-click to reset"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={() => setChatWidth(CHAT_MIN_PX)}
    />
  )
}
