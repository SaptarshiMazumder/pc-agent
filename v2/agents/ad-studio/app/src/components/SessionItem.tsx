import { useEffect, useRef, useState } from 'react'
import { Loader2, MoreHorizontal } from 'lucide-react'

import { when, type ChatRow } from '../agentd/sessions'
import ChatMenu from './ChatMenu'
import { useHoverTip } from './HoverTip'

/**
 * One saved-conversation row.
 *
 * ONE LINE, NOT TWO. This used to show the title in bold over a snippet of the first message, which
 * is two facts where the list only needs one: you pick a conversation by its name, and the snippet
 * was a second ellipsised string that made every row twice as tall and half as scannable. The
 * snippet moved into the hover tooltip, where reading it is a choice.
 *
 * TITLES ARE LONGER THAN THE RAIL IS WIDE — they are generated from the first message — so the row
 * ellipsises and the tooltip carries the full name plus the meta. It is PORTALLED (see HoverTip)
 * because the sidebar scrolls and would otherwise clip it.
 *
 * THE ACTIONS ARE A "⋯" MENU, not controls on the row. A trash icon that appears under the cursor,
 * one pixel from the row you are trying to open, in a list you scan constantly, is a destructive
 * control at rest; the menu is one step further away and can hold rename and duplicate as well
 * without the row growing. What is offered is whatever the window passed a handler for.
 *
 * DOUBLE-CLICK RENAMES IN PLACE. The menu's Rename opens the same input — the double-click is the
 * shortcut for people who already know it is there.
 */
export default function SessionItem({
  session,
  active,
  busy = false,
  onOpen,
  onRename,
  onDuplicate,
  onDelete,
}: {
  session: ChatRow
  active: boolean
  /** A delete is in flight for this row: the ⋯ becomes a spinner and the menu cannot reopen. */
  busy?: boolean
  onOpen: () => void
  /** Commit a new title. Absent = the item is not offered and double-click does nothing. */
  onRename?: (title: string) => void
  onDuplicate?: () => void
  onDelete?: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [menu, setMenu] = useState<DOMRect | null>(null) // ⋯ anchor; open when set
  const tip = useHoverTip()
  const ref = useRef<HTMLInputElement>(null)
  const label = session.title || 'Untitled'
  /* WHAT THE ROW NO LONGER SHOWS, gathered for the tooltip: the snippet first, because that is
     what the second line used to carry, then the size and age of the conversation. */
  const meta = [
    session.snippet || '',
    session.messages ? `${session.messages} msgs` : '',
    when(session.modified),
  ]
    .filter(Boolean)
    .join(' · ')
  const hasMenu = !!(onRename || onDuplicate || onDelete)

  useEffect(() => {
    if (editing) ref.current?.select()
  }, [editing])

  function commit(): void {
    // NOT guarded against an empty title: clearing it is how a manual name comes back off and
    // auto-titling resumes (see renameSession in agentd/sessions.ts).
    onRename?.(draft.trim())
    setEditing(false)
  }

  if (editing) {
    return (
      <input
        ref={ref}
        className="rename-input"
        value={draft}
        autoFocus
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            setEditing(false)
          }
        }}
      />
    )
  }

  const startRename = (): void => {
    if (!onRename) return
    setDraft(label)
    setEditing(true)
  }

  return (
    <button
      className={`row session-row ${active ? 'active' : ''} ${menu ? 'menu-open' : ''}`}
      onClick={onOpen}
      onDoubleClick={startRename}
      // Suppressed while the menu is open so the tooltip cannot cover what you just opened.
      {...(menu ? {} : tip.bind(label, meta))}
    >
      <span className="row-main">
        <span className="row-title">
          {session.running && <span className="row-live" title="running" />}
          {label}
        </span>
      </span>
      {hasMenu && (
        <span className="row-actions">
          {busy ? (
            <span className="hover-btn is-busy" title="Deleting…">
              <Loader2 size={15} className="ld-spin" />
            </span>
          ) : (
            <span
              className="hover-btn"
              title="more"
              aria-label="Conversation actions"
              onClick={(e) => {
                e.stopPropagation()
                tip.hide()
                setMenu(e.currentTarget.getBoundingClientRect())
              }}
            >
              <MoreHorizontal size={15} />
            </span>
          )}
        </span>
      )}
      {tip.node}
      {menu && (
        <ChatMenu
          anchor={menu}
          onClose={() => setMenu(null)}
          onRename={onRename ? startRename : undefined}
          onDuplicate={onDuplicate}
          onDelete={onDelete}
        />
      )}
    </button>
  )
}
