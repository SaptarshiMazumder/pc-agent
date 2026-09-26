/* A Library workflow, drawn exactly as the Workspace draws one (WorkflowItem).
 *
 * THE SAME CARD, NOT A LOOK-ALIKE. A kept workflow shows its run file and ComfyUI file with a
 * download each, and its installer when one was saved with it — the same block the chat's
 * Workflow tab shows. What differs is only what can be done from here: use it in a chat, run it
 * again in a fresh one, view the graph; and the Library's own note under it.
 *
 * ITS FILES ARE LISTED WHEN THE CARD IS DRAWN, one folder per card (the version's), because the
 * catalogue records names, not files. A version folder that is gone says so on the card rather
 * than drawing an empty one.
 */

import { FileCode2, Play } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import { fileUrl, type Artifact } from '../../agentd/artifacts'
import { latestVersion, libraryFiles, type LibraryItem } from '../../agentd/library'
import { collectWorkflows } from '../workflows/WorkflowCard'
import { INSTALLER, WorkflowItem } from '../workflows/WorkflowItem'

export function LibraryWorkflowItem({
  item,
  client,
  busy,
  useLabel,
  onUse,
  onRunAgain,
  onDelete,
  onNote,
  version,
}: {
  item: LibraryItem
  client: AgentdClient | undefined
  busy: boolean
  /** "Use in this chat" in a chat's stage, "Use in new chat" on the Library page. Omitted → no
   *  Use button (a template's steps are used as a whole, from the template). */
  useLabel?: string
  onUse?: (item: LibraryItem) => void
  onRunAgain?: (item: LibraryItem) => void
  onDelete?: () => void
  onNote?: (item: LibraryItem, note: string) => void
  /** A template's step pins its own folder; a Library workflow shows its latest version. */
  version?: number
}) {
  const [files, setFiles] = useState<Artifact[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    if (!client) return
    libraryFiles(client, item, version)
      .then((f) => alive && setFiles(f))
      .catch((e) => alive && setError(String((e as Error)?.message || e)))
    return () => {
      alive = false
    }
  }, [client, item, version])

  // Installer files first: a `.manifest.json` would otherwise pair as a graph of its own.
  const installers = (files || []).filter((f) => INSTALLER.test(f.name))
  const wf = files ? collectWorkflows(files.filter((f) => !INSTALLER.test(f.name)))[0] : undefined
  const v = version || latestVersion(item)
  const slots = item.versions.find((x) => x.v === v)?.slots || []
  const meta = [v ? `v${v}` : '', slots.length ? `slots: ${slots.map((s) => `@${s}`).join(', ')}` : '', item.from?.title ? `from: ${item.from.title}` : '']
    .filter(Boolean)
    .join(' · ')

  if (error) return <p className="lib-error">{item.name}: {error}</p>
  if (!files) return <div className="wp-item lib-loading-card">{item.name}…</div>
  if (!wf) return <p className="lib-error">{item.name}: no workflow files in this version.</p>

  return (
    <WorkflowItem
      wf={{ ...wf, name: item.name }}
      installers={installers}
      meta={meta}
      onDelete={onDelete ? () => onDelete() : undefined}
      actions={
        <>
          {useLabel && onUse && (
            <button
              type="button"
              className="wp-btn is-primary"
              disabled={busy}
              title={busy ? 'Wait for the current turn to finish' : 'Put this workflow in the chat box — you send it'}
              onClick={() => onUse(item)}
            >
              {useLabel}
            </button>
          )}
          {onRunAgain && (
            <button type="button" className="wp-btn" onClick={() => onRunAgain(item)} title="Start a new conversation that runs this workflow again">
              <Play size={14} strokeWidth={1.9} /> Run again
            </button>
          )}
          {wf.api && (
            <a className="wp-btn" href={fileUrl(wf.api.path)} target="_blank" rel="noopener noreferrer">
              <FileCode2 size={14} strokeWidth={1.9} /> View graph
            </a>
          )}
        </>
      }
    >
      {onNote && <LibraryNote item={item} onNote={onNote} />}
    </WorkflowItem>
  )
}

/** The Library's own line under a kept workflow: a note, for the person. */
function LibraryNote({ item, onNote }: { item: LibraryItem; onNote: (item: LibraryItem, note: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(item.note)
  if (!editing) {
    return (
      <button type="button" className="lib-note" onClick={() => setEditing(true)} title="Edit the note">
        {item.note || 'add a note'}
      </button>
    )
  }
  return (
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
  )
}

export default LibraryWorkflowItem
