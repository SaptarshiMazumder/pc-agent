import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Copy, Pencil, Trash2 } from 'lucide-react'

/**
 * The per-chat "⋯" action menu. Rendered through a portal + fixed positioning so it escapes the
 * sidebar's overflow/scroll-mask, and clamped into the viewport. Because a portal still bubbles
 * React events to its parent, the container stops propagation so clicks never reach the row.
 *
 * COPIED FROM agentd, minus two of its five items:
 *
 *   Move to project     this window has no projects — `projects.list` is host-only and the daemon
 *                       refuses it on an app connection, so the row could only ever be empty
 *   Export as Markdown  would be the first thing in this window that writes a file to disk
 *
 * Delete is agentd's, verbatim: the same two-step arm (first click reads "Click again to
 * delete"), the same danger styling, the same separator above it. An earlier copy left it out
 * with a note claiming agentd had no confirmation step — it does, the arming IS the
 * confirmation, and a Recents list that can only grow is how it became a wall of
 * indistinguishable "[context] We are working…" rows.
 */
export default function ChatMenu({
  anchor,
  onClose,
  onRename,
  onDuplicate,
  onDelete,
}: {
  anchor: DOMRect
  onClose: () => void
  onRename: () => void
  onDuplicate: () => void
  onDelete: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [armed, setArmed] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number }>({
    left: anchor.left,
    top: anchor.bottom + 4,
  })

  // right-align the menu under the ⋯ button, flip up if it would overflow the bottom
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const w = el.offsetWidth
    const h = el.offsetHeight
    const pad = 8
    let left = anchor.right - w
    let top = anchor.bottom + 4
    if (left < pad) left = pad
    if (left + w > window.innerWidth - pad) left = window.innerWidth - pad - w
    if (top + h > window.innerHeight - pad) top = Math.max(pad, anchor.top - h - 4)
    setPos({ left, top })
  }, [anchor])

  // close on outside click / Escape
  useEffect(() => {
    const onDown = (e: MouseEvent): void => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const run = (fn: () => void): void => {
    fn()
    onClose()
  }

  return createPortal(
    <div
      className="chat-menu"
      ref={ref}
      role="menu"
      style={{ left: pos.left, top: pos.top }}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <button className="chat-menu-item" onClick={() => run(onRename)}>
        <span className="cm-ico">
          <Pencil size={15} />
        </span>
        Rename
      </button>
      <button className="chat-menu-item" onClick={() => run(onDuplicate)}>
        <span className="cm-ico">
          <Copy size={15} />
        </span>
        Duplicate
      </button>
      <div className="chat-menu-sep" />
      <button
        className="chat-menu-item danger"
        onClick={() => (armed ? run(onDelete) : setArmed(true))}
      >
        <span className="cm-ico">
          <Trash2 size={15} />
        </span>
        {armed ? 'Click again to delete' : 'Delete'}
      </button>
    </div>,
    document.body,
  )
}
