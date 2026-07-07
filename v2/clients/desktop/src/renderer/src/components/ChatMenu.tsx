import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Pencil, FolderInput, FileDown, Copy, Trash2, ChevronRight, Check, CornerUpLeft } from 'lucide-react'

import type { ProjectRow } from '../gateway/protocol'

/**
 * The per-chat "⋯" action menu (Rename · Move to project · Export as Markdown · Duplicate ·
 * Delete). Rendered through a portal + fixed positioning so it escapes the sidebar's
 * overflow/scroll-mask, and clamped into the viewport. "Move to project" expands an inline
 * sub-pane (no flyout), and Delete is a two-step confirm. Because a portal still bubbles
 * React events to its parent, the container stops propagation so clicks never reach the row.
 */
export default function ChatMenu({
  anchor,
  projects,
  currentProjectId,
  onClose,
  onRename,
  onDelete,
  onDuplicate,
  onExport,
  onMove
}: {
  anchor: DOMRect
  projects: ProjectRow[]
  currentProjectId?: string
  onClose: () => void
  onRename: () => void
  onDelete: () => void
  onDuplicate: () => void
  onExport: () => void
  onMove: (projectId: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [movePane, setMovePane] = useState(false)
  const [armed, setArmed] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: anchor.left, top: anchor.bottom + 4 })

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
  }, [anchor, movePane])

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
    >
      <button className="chat-menu-item" onClick={() => run(onRename)}>
        <span className="cm-ico"><Pencil size={15} /></span>Rename
      </button>
      <button className="chat-menu-item" onClick={() => setMovePane((v) => !v)}>
        <span className="cm-ico"><FolderInput size={15} /></span>Move to project
        <span className="cm-chev" style={{ transform: movePane ? 'rotate(90deg)' : 'none' }}><ChevronRight size={14} /></span>
      </button>
      {movePane && (
        <div className="chat-menu-sub">
          <button className="chat-menu-item" onClick={() => run(() => onMove(''))}>
            <span className="cm-ico"><CornerUpLeft size={14} /></span>No project
            {!currentProjectId && <span className="chat-menu-check"><Check size={14} /></span>}
          </button>
          {projects.map((p) => (
            <button key={p.id} className="chat-menu-item" onClick={() => run(() => onMove(p.id))}>
              <span className="row-title" style={{ flex: 1 }}>{p.name}</span>
              {currentProjectId === p.id && <span className="chat-menu-check"><Check size={14} /></span>}
            </button>
          ))}
          {projects.length === 0 && <div className="row-sub" style={{ padding: '5px 9px' }}>no projects yet</div>}
        </div>
      )}
      <button className="chat-menu-item" onClick={() => run(onExport)}>
        <span className="cm-ico"><FileDown size={15} /></span>Export as Markdown
      </button>
      <button className="chat-menu-item" onClick={() => run(onDuplicate)}>
        <span className="cm-ico"><Copy size={15} /></span>Duplicate
      </button>
      <div className="chat-menu-sep" />
      <button
        className="chat-menu-item danger"
        onClick={() => (armed ? run(onDelete) : setArmed(true))}
      >
        <span className="cm-ico"><Trash2 size={15} /></span>{armed ? 'Click again to delete' : 'Delete'}
      </button>
    </div>,
    document.body
  )
}
