import { useMemo, useState } from 'react'
import { Folder, Plus, Pencil, Trash2 } from 'lucide-react'

import { hashColor } from '../lib/agentPresentation'
import { whenLabel } from '../lib/timefmt'
import { useApp } from '../state/store'

/** Projects list page (view:'projects'). Mirrors ChatGPT's Projects page: a single flat list
 *  with per-project chat counts + last-activity, computed client-side from the cross-agent
 *  Recents (each row carries its projectId). Click a project → its detail page. */
export default function ProjectsView() {
  const projects = useApp((s) => s.projects)
  const recents = useApp((s) => s.recents)
  const createProject = useApp((s) => s.createProject)
  const renameProject = useApp((s) => s.renameProject)
  const deleteProject = useApp((s) => s.deleteProject)
  const openProject = useApp((s) => s.openProject)

  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [armed, setArmed] = useState<string | null>(null)

  // chat count + last activity per project, from the cross-agent recents
  const stats = useMemo(() => {
    const m = new Map<string, { count: number; modified: number }>()
    for (const r of recents) {
      if (!r.projectId) continue
      const cur = m.get(r.projectId) || { count: 0, modified: 0 }
      cur.count += 1
      cur.modified = Math.max(cur.modified, r.modified)
      m.set(r.projectId, cur)
    }
    return m
  }, [recents])

  const q = query.trim().toLowerCase()
  const shown = projects.filter((p) => !q || p.name.toLowerCase().includes(q))

  function submitNew() {
    const name = draft.trim()
    setAdding(false)
    setDraft('')
    if (name) void createProject(name) // opens a fresh chat inside the new project
  }

  return (
    <div className="settings">
      <div className="settings-inner settings-wide">
        <div className="settings-head">
          <div className="settings-head-titles">
            <div className="page-title">Projects</div>
            <div className="page-sub">Group chats and files around a goal. Message a project and its lead agent responds.</div>
          </div>
          <div className="settings-head-actions">
            <input
              className="field-input head-search"
              placeholder="Search projects"
              value={query}
              spellCheck={false}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button className="btn primary" onClick={() => { setAdding(true); setDraft('') }}>
              <Plus size={14} />New
            </button>
          </div>
        </div>

        {adding && (
          <div className="settings-card ds-form">
            <div className="field">
              <span className="field-label">Project name</span>
              <input
                className="field-input"
                placeholder="e.g. Q3 Report"
                value={draft}
                autoFocus
                spellCheck={false}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={submitNew}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); submitNew() }
                  else if (e.key === 'Escape') { e.preventDefault(); setAdding(false) }
                }}
              />
            </div>
          </div>
        )}

        {shown.length === 0 ? (
          <div className="settings-empty">
            {q ? 'No projects match.' : 'No projects yet — New to create one.'}
          </div>
        ) : (
          <div className="entity-list">
            {shown.map((p) => {
              const st = stats.get(p.id)
              const isRenaming = renaming === p.id
              const isArmed = armed === p.id
              if (isRenaming) {
                return (
                  <input
                    key={p.id}
                    className="rename-input"
                    value={renameDraft}
                    autoFocus
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onBlur={() => { if (renameDraft.trim()) void renameProject(p.id, renameDraft.trim()); setRenaming(null) }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); if (renameDraft.trim()) void renameProject(p.id, renameDraft.trim()); setRenaming(null) }
                      else if (e.key === 'Escape') { e.preventDefault(); setRenaming(null) }
                    }}
                  />
                )
              }
              return (
                <div className="entity-row" key={p.id} onClick={() => openProject(p.id)}>
                  <span className="entity-ico ico-tint" style={{ color: hashColor(p.id) }}><Folder size={17} /></span>
                  <span className="entity-main">
                    <span className="entity-name">{p.name}</span>
                    <span className="entity-sub">
                      {st?.count || 0} chat{(st?.count || 0) === 1 ? '' : 's'}
                      {st?.modified ? ` · ${whenLabel(st.modified * 1000)}` : ''}
                    </span>
                  </span>
                  <button
                    className="hover-btn"
                    title="rename project"
                    onClick={(e) => { e.stopPropagation(); setRenameDraft(p.name); setRenaming(p.id) }}
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    className={`hover-btn ${isArmed ? 'danger' : ''}`}
                    title={isArmed ? 'click again to delete (chats become standalone)' : 'delete project'}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (isArmed) { void deleteProject(p.id); setArmed(null) }
                      else { setArmed(p.id); setTimeout(() => setArmed((a) => (a === p.id ? null : a)), 3000) }
                    }}
                  >
                    {isArmed ? <span className="confirm-text">sure?</span> : <Trash2 size={15} />}
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
