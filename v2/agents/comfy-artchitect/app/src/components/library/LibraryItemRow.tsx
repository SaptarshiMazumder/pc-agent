/* One Library item as a row: what it is, where it came from, and what can be done with it.
 *
 * A WORKFLOW IS USED THROUGH THE AGENT. "Use in this chat" puts one sentence in the chat box
 * (never sends it) and, once sent, the agent calls library_use, which copies the graph in and records its slots — so the
 * design exists the way an emitted one does. The window could copy two files itself, but the
 * agent would then never know they arrived. "Run again" does the same in a fresh conversation.
 *
 * A REFERENCE IS USED BY THE WINDOW. Pick the slot it fills (this chat's declared roles, or
 * "as itself") and the file is copied on the daemon's disk into references/<chat>/, exactly
 * where the References panel would have put an upload.
 */

import { ExternalLink, FileJson, FileText, Image as ImageIcon, Play, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import { fileUrl, thumbnailUrl, type Artifact } from '../../agentd/artifacts'
import { latestVersion, libraryFiles, type LibraryItem } from '../../agentd/library'
import type { Slot } from '../../agentd/reference-slots'

const OTHER = '__other__'

/** A reference drawn as itself: an image's bounded thumbnail, a video's first frame. Anything
 *  else — audio, or a file the listing did not return — keeps the kind's icon. */
function LibraryThumb({ file, fallback }: { file?: Artifact; fallback: JSX.Element }) {
  const [failed, setFailed] = useState(false)
  if (file?.kind === 'image' && !failed) {
    return <img className="lib-thumb" src={thumbnailUrl(file.path)} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
  }
  if (file?.kind === 'video') {
    return <video className="lib-thumb" src={fileUrl(file.path)} muted preload="metadata" playsInline />
  }
  return fallback
}

export function LibraryItemRow({
  item,
  media,
  client,
  slots,
  targetRole,
  busy,
  useLabel,
  onUseWorkflow,
  onRunAgain,
  onUseReference,
  onDelete,
  onNote,
}: {
  item: LibraryItem
  /** A reference's file on disk, for its thumbnail. */
  media?: Artifact
  client: AgentdClient | undefined
  slots: Slot[]
  /** A slot waiting for a reference: preselected here so Use is one click. */
  targetRole: string
  busy: boolean
  /** The workflow Use button: "Use in this chat" in the stage, "Use in new chat" on the page. */
  useLabel: string
  onUseWorkflow: (item: LibraryItem) => void
  onRunAgain: (item: LibraryItem) => void
  onUseReference: (item: LibraryItem, role: string) => void
  onDelete: () => void
  onNote: (item: LibraryItem, note: string) => void
}) {
  const [role, setRole] = useState<string>(
    () => targetRole || slots.find((s) => !s.file)?.role || slots[0]?.role || OTHER,
  )
  useEffect(() => {
    if (targetRole) setRole(targetRole)
  }, [targetRole])
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(item.note)

  const Icon = item.kind === 'workflow' ? FileJson : item.kind === 'reference' ? ImageIcon : FileText
  const v = latestVersion(item)
  const slotsOfLatest = item.versions.find((x) => x.v === v)?.slots || []
  const meta = [
    item.kind === 'workflow' ? `v${v || 1}` : '',
    item.kind === 'workflow' && slotsOfLatest.length ? `slots: ${slotsOfLatest.map((s) => `@${s}`).join(', ')}` : '',
    item.from?.title ? `from: ${item.from.title}` : '',
  ].filter(Boolean)

  const open = async (): Promise<void> => {
    if (!client) return
    const files = await libraryFiles(client, item)
    const f = files.find((x) => x.name.endsWith('.api.json')) || files[0]
    if (f) window.open(fileUrl(f.path), '_blank', 'noopener')
  }

  const roleOptions = slots.map((s) => s.role)
  if (targetRole && !roleOptions.includes(targetRole)) roleOptions.unshift(targetRole)

  return (
    <div className={`lib-row lib-row-${item.kind}`}>
      <LibraryThumb
        file={media}
        fallback={
          <span className="lib-ico">
            <Icon size={15} strokeWidth={1.7} />
          </span>
        }
      />
      <div className="lib-main">
        <span className="lib-name">{item.name}</span>
        <span className="lib-meta">{meta.join(' · ')}</span>
        {editing ? (
          <input
            className="lib-note-input"
            value={draft}
            placeholder="a note, for you"
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              setEditing(false)
              if (draft.trim() !== item.note) onNote(item, draft)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
              if (e.key === 'Escape') {
                setDraft(item.note)
                setEditing(false)
              }
            }}
          />
        ) : (
          <button type="button" className="lib-note" onClick={() => setEditing(true)} title="Edit the note">
            {item.note || 'add a note'}
          </button>
        )}
      </div>
      <div className="lib-actions">
        {item.kind === 'workflow' && (
          <>
            <button
              type="button"
              className="lib-btn lib-btn-use"
              disabled={busy}
              title={busy ? 'Wait for the current turn to finish' : 'Put this workflow in the chat box — you send it'}
              onClick={() => onUseWorkflow(item)}
            >
              {useLabel}
            </button>
            <button
              type="button"
              className="lib-btn"
              title="Start a new conversation that runs this workflow again"
              onClick={() => onRunAgain(item)}
            >
              <Play size={13} strokeWidth={1.8} /> Run again
            </button>
          </>
        )}
        {item.kind === 'reference' && (
          <>
            <select
              className="lib-select"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              title="Which slot this fills"
            >
              {roleOptions.map((r) => {
                const s = slots.find((x) => x.role === r)
                return (
                  <option key={r} value={r}>
                    @{r}
                    {s?.file ? ' (replace)' : ''}
                  </option>
                )
              })}
              <option value={OTHER}>as itself</option>
            </select>
            <button
              type="button"
              className="lib-btn lib-btn-use"
              disabled={busy}
              title="Copy this file into this chat's references"
              onClick={() => onUseReference(item, role === OTHER ? '' : role)}
            >
              Use
            </button>
          </>
        )}
        <button type="button" className="lib-btn" onClick={() => void open()} title="Open the file">
          <ExternalLink size={13} strokeWidth={1.8} />
        </button>
        <button type="button" className="lib-btn lib-btn-del" onClick={onDelete} title="Delete from the Library">
          <Trash2 size={13} strokeWidth={1.8} />
        </button>
      </div>
    </div>
  )
}
