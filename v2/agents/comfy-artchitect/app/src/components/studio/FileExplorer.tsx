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
 * nothing is capped, and a click OPENS the file (see FileModal) rather than navigating away.
 */

import { useMemo, useState } from 'react'
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
import { FileModal } from './FileModal'

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

function buildTree(artifacts: Artifact[]): Dir {
  const root: Dir = { name: '', dirs: new Map(), files: [] }
  for (const a of artifacts) {
    const parts = relative(a.path).split('/').filter(Boolean)
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
}: {
  dir: Dir
  depth: number
  onOpen: (a: Artifact) => void
}) {
  return (
    <>
      {[...dir.dirs.values()]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((child) => (
          <DirRow key={child.name} dir={child} depth={depth} onOpen={onOpen} />
        ))}
      {[...dir.files]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((f) => (
          <button
            key={f.path}
            className="fx-row fx-file"
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => onOpen(f)}
            title={f.name}
          >
            <span className="fx-ico">{iconFor(f)}</span>
            <span className="fx-name st-mono">{f.name}</span>
            {f.size ? <span className="fx-size">{humanSize(f.size)}</span> : null}
          </button>
        ))}
    </>
  )
}

function DirRow({ dir, depth, onOpen }: { dir: Dir; depth: number; onOpen: (a: Artifact) => void }) {
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
      {open && <DirRows dir={dir} depth={depth + 1} onOpen={onOpen} />}
    </>
  )
}

export function FileExplorer({ artifacts }: { artifacts: Artifact[] }) {
  const tree = useMemo(() => buildTree(artifacts), [artifacts])
  const [open, setOpen] = useState<Artifact | null>(null)
  const total = useMemo(() => count(tree), [tree])

  return (
    <>
      <div className="st-panel-head">
        <h2>Files</h2>
        <span className="st-panel-note">{total} in this workspace</span>
      </div>
      {total === 0 ? (
        <p className="st-empty">
          Nothing written yet — workflows, renders and downloads all land here as the agent makes
          them.
        </p>
      ) : (
        <div className="fx-tree">
          <DirRows dir={tree} depth={0} onOpen={setOpen} />
        </div>
      )}
      {open && <FileModal file={open} onClose={() => setOpen(null)} />}
    </>
  )
}
