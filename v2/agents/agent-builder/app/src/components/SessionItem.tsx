import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'

import { when, type ChatRow } from '../agentd/sessions'
import { useApp } from '../state/store'
import ChatMenu from './ChatMenu'
import { useHoverTip } from './HoverTip'

/** One saved-chat row: hover tooltip (full name · meta), double-click to rename, and a ⋯ menu
 *  (rename · duplicate) on hover.
 *
 *  COPIED FROM agentd. Two of its features are absent because the fact behind them does not exist
 *  here: the leading AGENT DOT (agentd colours each recent by the agent it belongs to, and every
 *  conversation in this window belongs to the same one, so the dot would be a signal carrying no
 *  information) and the trailing PROJECT BADGE (no projects on an app connection).
 *
 *  THE TOOLTIP IS WHERE THE FULL TITLE LIVES. Titles are auto-generated from the first message and
 *  routinely longer than the rail is wide, so the row ellipsises and the tooltip — portalled, so
 *  the sidebar's overflow cannot clip it — is what lets you read one without opening it. */
export default function SessionItem({
  session,
  active,
  onOpen,
}: {
  session: ChatRow
  active: boolean
  onOpen: () => void
}) {
  const renameSession = useApp((s) => s.renameSession)
  const duplicateSession = useApp((s) => s.duplicateSession)
  const deleteSession = useApp((s) => s.deleteSession)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [menu, setMenu] = useState<DOMRect | null>(null) // ⋯ menu anchor (open when set)
  const tip = useHoverTip()
  const ref = useRef<HTMLInputElement>(null)
  const label = session.title || session.sessionId
  const meta = [
    session.messages ? `${session.messages} msgs` : '',
    when(session.modified),
  ]
    .filter(Boolean)
    .join(' · ')

  useEffect(() => {
    if (editing) ref.current?.select()
  }, [editing])

  function commit() {
    // NOT guarded against an empty title: clearing it is how you take a manual name back off and
    // let auto-titling resume. See renameSession in agentd/sessions.ts.
    void renameSession(session.sessionId, draft.trim())
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

  return (
    <button
      className={`row session-row ${active ? 'active' : ''} ${menu ? 'menu-open' : ''}`}
      onClick={onOpen}
      onDoubleClick={() => {
        setDraft(label)
        setEditing(true)
      }}
      // The tooltip is suppressed while the menu is open, so it cannot cover the thing you just
      // opened.
      {...(menu ? {} : tip.bind(label, meta))}
    >
      <span className="row-main">
        <span className="row-title">{label}</span>
      </span>
      <span className="row-actions">
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
      </span>
      {tip.node}
      {menu && (
        <ChatMenu
          anchor={menu}
          onClose={() => setMenu(null)}
          onRename={() => {
            setDraft(label)
            setEditing(true)
          }}
          onDuplicate={() => void duplicateSession(session.sessionId)}
          onDelete={() => void deleteSession(session.sessionId)}
        />
      )}
    </button>
  )
}
