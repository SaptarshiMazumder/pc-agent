import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'

import type { SessionRow } from '../gateway/protocol'
import { agentColor } from '../lib/agentPresentation'
import { whenLabel, whenTimeLabel } from '../lib/timefmt'
import { useApp } from '../state/store'
import ChatMenu from './ChatMenu'
import { useHoverTip } from './HoverTip'

/** One saved-chat row: hover tooltip (agent · full name · meta), double-click to rename, and a ⋯
 *  menu (rename · move to project · export md · duplicate · delete) on hover. In cross-agent lists
 *  it shows a leading agent dot; in mixed-project lists it shows a trailing project badge. Shared by
 *  the sidebar Recents, the Project detail page, and the Agent detail page.
 *  `table` = the WIDE variant for entity pages (inside a `.chat-table` with a Name/Modified
 *  header): divider lines between rows + a right-aligned date·time column. */
export default function SessionItem({
  session,
  active,
  onOpen,
  withAgentDot = false,
  withProjectBadge = false,
  table = false
}: {
  session: SessionRow
  active: boolean
  onOpen: () => void
  withAgentDot?: boolean
  withProjectBadge?: boolean
  table?: boolean
}) {
  const renameSession = useApp((s) => s.renameSession)
  const deleteSession = useApp((s) => s.deleteSession)
  const moveSession = useApp((s) => s.moveSession)
  const duplicateSession = useApp((s) => s.duplicateSession)
  const exportSessionMd = useApp((s) => s.exportSessionMd)
  const projects = useApp((s) => s.projects)
  const agents = useApp((s) => s.agents)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [menu, setMenu] = useState<DOMRect | null>(null) // ⋯ menu anchor (open when set)
  const tip = useHoverTip()
  const ref = useRef<HTMLInputElement>(null)
  const label = session.title || session.sessionId
  const agent = agents.find((a) => a.id === session.agentId)
  const project = session.projectId ? projects.find((p) => p.id === session.projectId) : undefined
  const meta = `${agent ? agent.name + ' · ' : ''}${session.messages} msgs · ${whenLabel(session.modified * 1000)}`

  useEffect(() => {
    if (editing) ref.current?.select()
  }, [editing])

  function commit() {
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
          if (e.key === 'Enter') { e.preventDefault(); commit() }
          else if (e.key === 'Escape') { e.preventDefault(); setEditing(false) }
        }}
      />
    )
  }

  return (
    <button
      className={`row session-row ${active ? 'active' : ''} ${menu ? 'menu-open' : ''}`}
      onClick={onOpen}
      onDoubleClick={() => { setDraft(label); setEditing(true) }}
      {...(menu ? {} : tip.bind(label, meta))}
    >
      {withAgentDot && (
        <span
          className="session-dot"
          title={agent?.name || session.agentId}
          style={{ background: agentColor(agent?.color, session.agentId) }}
        />
      )}
      <span className="row-main">
        <span className="row-title">{label}</span>
        {table && session.snippet && <span className="row-snippet">{session.snippet}</span>}
      </span>
      {withProjectBadge && project && (
        <span className="proj-badge" title={`project: ${project.name}`}>{project.name}</span>
      )}
      {table && <span className="row-when">{whenTimeLabel(session.modified * 1000)}</span>}
      <span className="row-actions">
        <span
          className="hover-btn"
          title="more"
          onClick={(e) => { e.stopPropagation(); tip.hide(); setMenu(e.currentTarget.getBoundingClientRect()) }}
        >
          <MoreHorizontal size={15} />
        </span>
      </span>
      {tip.node}
      {menu && (
        <ChatMenu
          anchor={menu}
          projects={projects}
          currentProjectId={session.projectId}
          onClose={() => setMenu(null)}
          onRename={() => { setDraft(label); setEditing(true) }}
          onDelete={() => void deleteSession(session.sessionId)}
          onDuplicate={() => void duplicateSession(session.sessionId)}
          onExport={() => void exportSessionMd(session.sessionId)}
          onMove={(pid) => void moveSession(session.sessionId, pid)}
        />
      )}
    </button>
  )
}
