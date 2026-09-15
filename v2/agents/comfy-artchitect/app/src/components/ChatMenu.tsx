import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Copy, Pencil, Trash2 } from 'lucide-react'

/**
 * The per-chat "⋯" action menu.
 *
 * PORTALLED, because the rail scrolls and clips. A menu drawn inside the row is cut off by the
 * sidebar's own `overflow`, and the last row's menu — the one you reach for most — is the one that
 * loses the most. `position: fixed` in a portal escapes both, and the placement below is clamped
 * into the viewport so it never opens off-screen.
 *
 * A PORTAL STILL BUBBLES REACT EVENTS to the JSX parent, which here is the row button. Without the
 * two stop-propagation handlers, clicking "Rename" would also open the conversation behind the
 * menu.
 *
 * DELETE ARMS RATHER THAN ASKS. The first click re-labels the item, the second does it — no dialog,
 * no second surface, and nothing destructive at rest. This replaced a pair of ✓/✕ buttons that sat
 * on the row itself: they were live on every hover, one pixel from the row you were trying to open,
 * and they had to be styled as a danger control in a list you scan constantly. There is no undo
 * behind this — the daemon drops the transcript — so the arm IS the confirmation.
 *
 * EVERY ITEM IS OPTIONAL. A window that cannot fork a conversation passes no `onDuplicate` and gets
 * a menu without that row, rather than one that offers something it cannot do.
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
  onRename?: () => void
  onDuplicate?: () => void
  onDelete?: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [armed, setArmed] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number }>({
    left: anchor.left,
    top: anchor.bottom + 4,
  })

  /* RIGHT-ALIGNED under the ⋯, flipped above it when the bottom of the window is too close.
     Measured after paint (useLayoutEffect) because the clamp needs the menu's real height, and a
     menu that jumps on its first frame reads as a glitch. */
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
      {onRename && (
        <button className="chat-menu-item" onClick={() => run(onRename)}>
          <span className="cm-ico">
            <Pencil size={15} />
          </span>
          Rename
        </button>
      )}
      {onDuplicate && (
        <button className="chat-menu-item" onClick={() => run(onDuplicate)}>
          <span className="cm-ico">
            <Copy size={15} />
          </span>
          Duplicate
        </button>
      )}
      {onDelete && (
        <>
          {/* Only when something sits above it — a separator under nothing is a stray line. */}
          {(onRename || onDuplicate) && <div className="chat-menu-sep" />}
          <button
            className="chat-menu-item danger"
            onClick={() => (armed ? run(onDelete) : setArmed(true))}
          >
            <span className="cm-ico">
              <Trash2 size={15} />
            </span>
            {armed ? 'Click again to delete' : 'Delete'}
          </button>
        </>
      )}
    </div>,
    document.body,
  )
}
