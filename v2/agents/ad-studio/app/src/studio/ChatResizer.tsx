/* The handle between the studio and the chat (the chat is on the right). Drag left to widen the
 * chat; the studio keeps its own minimum, so neither side can be squeezed into uselessness. */

import { useRef } from 'react'

import { STUDIO_MIN_PX, useApp } from '../state/store'

export function ChatResizer() {
  const setWidth = useApp((s) => s.setChatWidth)
  const start = useRef<{ x: number; w: number } | null>(null)

  return (
    <div
      className="chat-resizer"
      role="separator"
      aria-orientation="vertical"
      title="Drag to resize"
      onPointerDown={(e) => {
        start.current = { x: e.clientX, w: useApp.getState().chatWidth }
        ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => {
        if (!start.current) return
        const row = (e.currentTarget.parentElement?.getBoundingClientRect().width || 0) - STUDIO_MIN_PX
        setWidth(Math.min(row, start.current.w - (e.clientX - start.current.x)))
      }}
      onPointerUp={() => (start.current = null)}
    />
  )
}
