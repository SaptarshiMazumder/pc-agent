/* Everything this agent has written, as the folder tree it actually is.
 *
 * WHY NOT THE FLAT CARD LIST IT REPLACES. `comfy_emit` writes files in PAIRS — `name.api.json`
 * (what the server runs) and `name.json` (what you import) — and the old cards truncated the name
 * with an ellipsis inside a narrow column, so both members rendered as the same string and the
 * panel looked like it was showing duplicates. The suffix that distinguishes them was the exact
 * character being cut. It also capped silently at six, and clicking a card threw raw JSON into a
 * browser tab.
 *
 * The paths already carry the structure (`workflows/`, `outputs/`, `references/`, `uploads/`), so
 * the tree is not invented — it is the workspace, shown. Full names wrap instead of truncating,
 * nothing is capped, and a click OPENS the file in the centre pane (see FileViewer) rather than
 * navigating away.
 */

import { useMemo, useState } from 'react'
import { BookmarkPlus, Trash2 } from 'lucide-react'
import {
  ChevronDown,
  ChevronRight,
  FileCode2,
  FileText,
  Film,
  Folder,
  FolderOpen,
  Image as ImageIcon,
  Music,
} from 'lucide-react'

import { humanSize, type Artifact } from '../../agentd/artifacts'
import { DeleteFilePrompt } from '../creations/DeleteFilePrompt'
import { CHAT_DIRS } from '../../agentd/workspace-files'
import { isCanvasImportable, setDragPayload } from './dragOut'

interface Dir {
  name: string
  dirs: Map<string, Dir>
  files: Artifact[]
}

/** The part of an absolute path that belongs to the user, not to the deployment.
 *
 *  An artifact's path is absolute and account-scoped
 *  (`/data/state/accounts/<id>/agents/<agent>/workspace/workflows/x.json`) — none of which is
 *  about the file. Everything after `workspace/` is. */
function relative(path: string): string {
  const norm = path.replace(/\\/g, '/')
  const at = norm.lastIndexOf('/workspace/')
  return at >= 0 ? norm.slice(at + '/workspace/'.length) : norm.split('/').pop() || norm
}

/** The path as the tree shows it: workspace-relative, without the chat's own folder. Each of this
 *  chat's folders (`workflows/<chat>/`, `outputs/<chat>/`, `references/<chat>/` — see
 *  agentd/workspace-files.ts) exists so that a workspace shared by every conversation can be
 *  listed per chat; the panel only ever holds one chat's files, so that segment tells the person
 *  reading nothing and is dropped. */
const CHAT_ROOTS = new Set<string>(CHAT_DIRS)

function displayParts(path: string): string[] {
  const parts = relative(path).split('/').filter(Boolean)
  if (parts.length >= 3 && CHAT_ROOTS.has(parts[0])) parts.splice(1, 1)
  return parts
}

function buildTree(artifacts: Artifact[]): Dir {
  const root: Dir = { name: '', dirs: new Map(), files: [] }
  for (const a of artifacts) {
    const parts = displayParts(a.path)
    const fileName = parts.pop()
    if (!fileName) continue
    let node = root
    for (const p of parts) {
      let next = node.dirs.get(p)
      if (!next) {
        next = { name: p, dirs: new Map(), files: [] }
        node.dirs.set(p, next)
      }
      node = next
    }
    // The same file can be declared by several turns; show it once.
    if (!node.files.some((f) => f.path === a.path)) node.files.push({ ...a, name: fileName })
  }
  return root
}

function iconFor(a: Artifact) {
  if (a.kind === 'image') return <ImageIcon size={14} strokeWidth={1.8} />
  if (a.kind === 'video') return <Film size={14} strokeWidth={1.8} />
  if (a.kind === 'audio') return <Music size={14} strokeWidth={1.8} />
  if (/\.(json|ya?ml|toml|py|js|ts|tsx|css|html|xml)$/i.test(a.name)) {
    return <FileCode2 size={14} strokeWidth={1.8} />
  }
  return <FileText size={14} strokeWidth={1.8} />
}

function count(dir: Dir): number {
  let n = dir.files.length
  for (const d of dir.dirs.values()) n += count(d)
  return n
}

function DirRows({
  dir,
  depth,
  onOpen,
  selectedPath,
  picked,
  onPick,
}: {
  dir: Dir
  depth: number
  onOpen: (a: Artifact) => void
  selectedPath?: string
  picked: Set<string>
  onPick: (path: string) => void
}) {
  return (
    <>
      {[...dir.dirs.values()]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((child) => (
          <DirRow
            key={child.name}
            dir={child}
            depth={depth}
            onOpen={onOpen}
            selectedPath={selectedPath}
            picked={picked}
            onPick={onPick}
          />
        ))}
      {[...dir.files]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((f) => (
          <div key={f.path} className={`fx-line${picked.has(f.path) ? ' is-picked' : ''}`}>
          {/* THE TICK SELECTS FOR DELETION and nothing else; opening is still the row. Separate
              controls because they are separate intents, and a row that both opened and armed a
              delete would make the more common click the more dangerous one. An <input> cannot
              live inside a <button>, hence the wrapper. */}
          <input
            type="checkbox"
            className="fx-pick"
            checked={picked.has(f.path)}
            onChange={() => onPick(f.path)}
            aria-label={`Select ${f.name} for deletion`}
            title="Select for deletion"
          />
          <button
            className={`fx-row fx-file${f.path === selectedPath ? ' is-on' : ''}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => onOpen(f)}
            /* DRAG A WORKFLOW STRAIGHT ONTO COMFYUI. Hover its tab mid-drag to switch, then drop
               on the canvas — no download, no file manager. See dragOut.ts. */
            draggable
            onDragStart={(e) => setDragPayload(e.dataTransfer, f)}
            title={
              isCanvasImportable(f)
                ? `${f.name} — drag onto your ComfyUI tab to load it`
                : f.name
            }
          >
            <span className="fx-ico">{iconFor(f)}</span>
            <span className="fx-name st-mono">{f.name}</span>
            {f.size ? <span className="fx-size">{humanSize(f.size)}</span> : null}
          </button>
          </div>
        ))}
    </>
  )
}

function DirRow({
  dir,
  depth,
  onOpen,
  selectedPath,
  picked,
  onPick,
}: {
  dir: Dir
  depth: number
  onOpen: (a: Artifact) => void
  selectedPath?: string
  picked: Set<string>
  onPick: (path: string) => void
}) {
  // Folders start OPEN: this panel exists to show what was made, and a tree that hides it behind
  // a disclosure is the flat list's problem in a new shape.
  const [open, setOpen] = useState(true)
  return (
    <>
      <button
        className="fx-row fx-dir"
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="fx-ico">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
        <span className="fx-ico">
          {open ? <FolderOpen size={14} strokeWidth={1.8} /> : <Folder size={14} strokeWidth={1.8} />}
        </span>
        <span className="fx-name">{dir.name}</span>
        <span className="fx-size">{count(dir)}</span>
      </button>
      {open && (
        <DirRows dir={dir} depth={depth + 1} onOpen={onOpen} selectedPath={selectedPath} picked={picked} onPick={onPick} />
      )}
    </>
  )
}

/** The rail. SELECTION LIVES IN THE PARENT now: the file being read is the studio's subject, not
 *  this component's private state — the centre pane renders it, and picking one here is what
 *  changes the whole screen. (It used to own a modal, which is why it could keep the selection to
 *  itself.) */
export function FileExplorer({
  artifacts,
  selected,
  onSelect,
  onDelete,
  onAddToLibrary,
  deletionDisabled = '',
}: {
  artifacts: Artifact[]
  selected?: Artifact | null
  onSelect: (a: Artifact) => void
  /** Delete these files (absolute paths, as listed) — the daemon does it, after one warning. */
  onDelete?: (paths: string[]) => Promise<void>
  /** Copy these files into the Library. Answers a sentence for the bar to show. */
  onAddToLibrary?: (paths: string[]) => Promise<string>
  /** Why a delete cannot happen right now (a run in flight) — shown on the button. */
  deletionDisabled?: string
}) {
  const tree = useMemo(() => buildTree(artifacts), [artifacts])
  const total = useMemo(() => count(tree), [tree])

  /* WHAT IS TICKED, by path. Kept here, not in the store: it is a half-formed intent that means
     nothing until the button is pressed, and a switch of chat should drop it. Pruned against the
     live list so a file that vanished (deleted, or the chat changed) cannot stay ticked. */
  const [picked, setPicked] = useState<Set<string>>(() => new Set())
  const alive = useMemo(() => new Set(artifacts.map((a) => a.path)), [artifacts])
  const chosen = useMemo(() => [...picked].filter((p) => alive.has(p)), [picked, alive])
  const onPick = (path: string): void =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (!next.delete(path)) next.add(path)
      return next
    })
  const [doomed, setDoomed] = useState<string[] | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const doDelete = async (): Promise<void> => {
    if (!doomed || !onDelete) return
    setDeleting(true)
    setDeleteError('')
    try {
      await onDelete(doomed)
      setDoomed(null)
      setPicked(new Set())
    } catch (e) {
      setDeleteError(String((e as Error)?.message || e))
    } finally {
      setDeleting(false)
    }
  }
  const save = async (): Promise<void> => {
    if (!chosen.length || !onAddToLibrary) return
    setSaving(true)
    try {
      setNotice(await onAddToLibrary(chosen))
      setPicked(new Set())
      setTimeout(() => setNotice(''), 4000)
    } catch (e) {
      setNotice(`Could not add to the Library: ${String((e as Error)?.message || e)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fx">
      <div className="fx-head">
        <span className="fx-title">Files</span>
        <span className="fx-count">{total}</span>
      </div>
      {/* THE BAR. Appears only once something is ticked, so the rail carries no control at rest.
          Two things a selection can become: gone (one warning, then the daemon deletes), or kept
          (copied into the Library, where every chat can reach it). Delete waits out a run — a
          file that vanishes between an emit and its run is the one case worth refusing. */}
      {chosen.length > 0 && (
        <div className="fx-delete">
          <span className="fx-delete-count">{chosen.length} selected</span>
          {onAddToLibrary && (
            <button
              type="button"
              className="fx-bar-btn"
              disabled={saving}
              title="Copy the selected files into your Library, shared by every conversation"
              onClick={() => void save()}
            >
              <BookmarkPlus size={13} strokeWidth={1.8} />
              {saving ? 'Adding…' : 'Add to Library'}
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              className="fx-delete-btn"
              disabled={!!deletionDisabled}
              title={deletionDisabled || 'Delete the selected files'}
              onClick={() => setDoomed(chosen)}
            >
              <Trash2 size={13} strokeWidth={1.8} />
              Delete
            </button>
          )}
          <button type="button" className="fx-delete-clear" onClick={() => setPicked(new Set())}>
            Clear
          </button>
        </div>
      )}
      {notice && <p className="fx-note">{notice}</p>}
      {doomed && (
        <DeleteFilePrompt
          names={doomed.map((p) => p.split('/').pop() || p)}
          busy={deleting}
          error={deleteError}
          onDelete={() => void doDelete()}
          onClose={() => setDoomed(null)}
        />
      )}
      {total === 0 ? (
        <p className="fx-empty">
          Nothing here yet — the references you add, and the workflows, renders and downloads the
          agent makes, all land here.
        </p>
      ) : (
        <div className="fx-tree">
          <DirRows
            dir={tree}
            depth={0}
            onOpen={onSelect}
            selectedPath={selected?.path}
            picked={picked}
            onPick={onPick}
          />
        </div>
      )}
    </div>
  )
}
