import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderPlus,
  Upload,
  Trash2,
  File,
  FileText,
  Image,
  Film,
  Music,
  RefreshCw
} from 'lucide-react'

import { gateway } from '../gateway/client'
import type { Artifact } from '../lib/artifacts'
import { humanSize } from '../lib/artifacts'
import { whenTimeLabel } from '../lib/timefmt'
import { useApp } from '../state/store'

/** One workspace entry as the daemon lists it (rel drives ops, path/abs feeds the canvas). */
interface WsEntry {
  name: string
  kind: 'folder' | 'image' | 'video' | 'audio' | 'file'
  size: number
  modified: number
  rel: string
  path: string
}

interface Scope {
  agentId?: string
  projectId?: string
}

function KindIcon({ kind }: { kind: WsEntry['kind'] }): JSX.Element {
  const s = 15
  if (kind === 'folder') return <Folder size={s} />
  if (kind === 'image') return <Image size={s} />
  if (kind === 'video') return <Film size={s} />
  if (kind === 'audio') return <Music size={s} />
  if (kind === 'file') return <File size={s} />
  return <FileText size={s} />
}

function toBase64(f: globalThis.File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
}

/**
 * Interactive workspace file TREE for an entity page (agent or project) — real hierarchy,
 * lazily loaded per directory via `workspace.list`. Files open in the Canvas (view/edit);
 * folders expand; upload / new-folder / delete act through the guarded workspace RPCs.
 */
export default function WorkspaceTree(scope: Scope): JSX.Element {
  const openCanvas = useApp((s) => s.openCanvas)
  const connection = useApp((s) => s.connection)
  // children per directory rel-path ('' = root); undefined = not loaded yet
  const [dirs, setDirs] = useState<Record<string, WsEntry[] | undefined>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [creatingIn, setCreatingIn] = useState<string | null>(null) // dir rel with an open "new folder" input
  const [draft, setDraft] = useState('')
  const [armed, setArmed] = useState<string | null>(null) // rel armed for delete
  const [uploadDir, setUploadDir] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const scopeParams = useCallback(
    () => (scope.projectId ? { projectId: scope.projectId } : { agentId: scope.agentId }),
    [scope.projectId, scope.agentId]
  )

  const loadDir = useCallback(
    async (rel: string): Promise<void> => {
      try {
        const res = await gateway.request<{ entries: WsEntry[]; error?: string }>('workspace.list', {
          ...scopeParams(),
          path: rel
        })
        if (res.error) setError(res.error)
        setDirs((d) => ({ ...d, [rel]: res.entries || [] }))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [scopeParams]
  )

  useEffect(() => {
    if (connection !== 'open') return
    setDirs({})
    setExpanded(new Set())
    setError('')
    void loadDir('')
  }, [connection, loadDir])

  /** reload a directory + every loaded dir beneath it (post-op refresh) */
  async function refresh(rel: string): Promise<void> {
    await loadDir(rel)
  }

  function toggle(entry: WsEntry): void {
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(entry.rel)) next.delete(entry.rel)
      else {
        next.add(entry.rel)
        if (dirs[entry.rel] === undefined) void loadDir(entry.rel)
      }
      return next
    })
  }

  function openFile(entry: WsEntry): void {
    const artifact: Artifact = { path: entry.path, name: entry.name, mime: '', kind: entry.kind === 'folder' ? 'file' : entry.kind, size: entry.size }
    openCanvas(artifact)
  }

  async function submitNewFolder(dirRel: string): Promise<void> {
    const name = draft.trim()
    setCreatingIn(null)
    setDraft('')
    if (!name) return
    await gateway.request('workspace.mkdir', { ...scopeParams(), path: dirRel ? `${dirRel}/${name}` : name })
    await refresh(dirRel)
    setExpanded((s) => new Set(s).add(dirRel))
  }

  async function pickUploads(list: FileList | null): Promise<void> {
    if (!list?.length) return
    for (const f of Array.from(list)) {
      const dataBase64 = await toBase64(f)
      await gateway.request('workspace.upload', { ...scopeParams(), path: uploadDir, name: f.name, dataBase64 })
    }
    await refresh(uploadDir)
  }

  async function remove(entry: WsEntry, parentRel: string): Promise<void> {
    setArmed(null)
    await gateway.request('workspace.delete', { ...scopeParams(), path: entry.rel })
    await refresh(parentRel)
  }

  function rows(parentRel: string, depth: number): JSX.Element[] {
    const entries = dirs[parentRel]
    if (entries === undefined) return [<div key={`${parentRel}-loading`} className="ws-note">loading…</div>]
    const out: JSX.Element[] = []
    for (const e of entries) {
      const isDir = e.kind === 'folder'
      const open = expanded.has(e.rel)
      const isArmed = armed === e.rel
      out.push(
        <div
          key={e.rel}
          className="ws-row"
          style={{ paddingLeft: 9 + depth * 18 }}
          onClick={() => (isDir ? toggle(e) : openFile(e))}
          title={isDir ? e.name : `open ${e.name} in the canvas`}
          role="button"
        >
          <span className="ws-caret">{isDir ? (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : null}</span>
          <span className={`ws-ico ${isDir ? 'ws-ico--dir' : ''}`}><KindIcon kind={e.kind} /></span>
          <span className="ws-name">{e.name}</span>
          <span className="ws-meta">
            {!isDir && e.size ? `${humanSize(e.size)} · ` : ''}
            {whenTimeLabel(e.modified * 1000)}
          </span>
          <span className="ws-actions" onClick={(ev) => ev.stopPropagation()}>
            {isDir && (
              <>
                <button className="hover-btn" title="upload files here" onClick={() => { setUploadDir(e.rel); fileRef.current?.click() }}>
                  <Upload size={14} />
                </button>
                <button className="hover-btn" title="new folder inside" onClick={() => { setCreatingIn(e.rel); setDraft(''); setExpanded((s) => new Set(s).add(e.rel)); if (dirs[e.rel] === undefined) void loadDir(e.rel) }}>
                  <FolderPlus size={14} />
                </button>
              </>
            )}
            <button
              className={`hover-btn ${isArmed ? 'danger' : ''}`}
              title={isArmed ? 'click again to delete permanently' : `delete ${isDir ? 'folder' : 'file'}`}
              onClick={() => {
                if (isArmed) void remove(e, parentRel)
                else { setArmed(e.rel); setTimeout(() => setArmed((a) => (a === e.rel ? null : a)), 3000) }
              }}
            >
              {isArmed ? <span className="confirm-text">sure?</span> : <Trash2 size={14} />}
            </button>
          </span>
        </div>
      )
      if (isDir && open) {
        if (creatingIn === e.rel) out.push(newFolderInput(e.rel, depth + 1))
        out.push(...rows(e.rel, depth + 1))
      }
    }
    if (!entries.length && parentRel !== '') {
      out.push(<div key={`${parentRel}-empty`} className="ws-note" style={{ paddingLeft: 9 + depth * 18 }}>empty</div>)
    }
    return out
  }

  function newFolderInput(dirRel: string, depth: number): JSX.Element {
    return (
      <input
        key={`newfolder-${dirRel}`}
        className="rename-input ws-newfolder"
        style={{ marginLeft: 9 + depth * 18 }}
        placeholder="folder name…"
        value={draft}
        autoFocus
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void submitNewFolder(dirRel)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); void submitNewFolder(dirRel) }
          else if (e.key === 'Escape') { e.preventDefault(); setCreatingIn(null); setDraft('') }
        }}
      />
    )
  }

  const root = dirs['']

  return (
    <div className="ws-tree">
      <div className="ws-toolbar">
        <button className="btn ghost" onClick={() => { setUploadDir(''); fileRef.current?.click() }}>
          <Upload size={14} />Upload
        </button>
        <button className="btn ghost" onClick={() => { setCreatingIn(''); setDraft('') }}>
          <FolderPlus size={14} />New folder
        </button>
        <button className="icon-btn icon-btn--sm push-end" title="refresh" onClick={() => void loadDir('')}>
          <RefreshCw size={14} />
        </button>
      </div>
      <input ref={fileRef} type="file" multiple hidden onChange={(e) => { void pickUploads(e.target.files); e.target.value = '' }} />
      {error && <div className="ws-note danger-text">{error}</div>}
      {creatingIn === '' && newFolderInput('', 0)}
      {root && root.length === 0 && creatingIn !== '' && (
        <div className="ws-note">No files yet — Upload or create a folder, or ask the agent to make something.</div>
      )}
      {rows('', 0)}
    </div>
  )
}
