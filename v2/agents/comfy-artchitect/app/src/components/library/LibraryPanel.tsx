/* The Library tab — a sibling of the Workspace in the studio, and the one place chats share.
 *
 * TWO SECTIONS, BY WHO PUT IT THERE. "Uploaded" is what the person brought from their machine;
 * "Saved from chats" is what came out of a conversation (the workspace rail's Add to Library, a
 * card's Save, a paste that was not an image). A saved item carries the chat it came from as a
 * caption and as a filter chip — never as a folder, which would be one more tree to dig
 * through.
 *
 * WHAT A ROW CAN DO. Use in this chat (a workflow is handed to the agent, which brings it in
 * with library_use so its slots are recorded; a reference is copied straight into a slot), Run
 * again (a fresh conversation around a kept workflow), open the file, edit its note, delete it
 * — one warning, then gone.
 *
 * NOBODY ARRIVES HERE BY INSTINCT, so the empty state says what the tab is for, and the doors
 * INTO it are elsewhere: the rail's selection bar, the cards in My creations, the From Library
 * button on every slot, and the composer, which sends a pasted non-image here and says so.
 * This panel is where things are kept, not where people discover that keeping is possible.
 */

import { Library, Upload } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import {
  deleteItem,
  readIndex,
  referenceFiles,
  updateNote,
  uploadToLibrary,
  useReferenceInChat,
  type LibraryItem,
  type LibraryOrigin,
} from '../../agentd/library'
import type { Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'
import { useApp } from '../../state/store'
import { DeleteFilePrompt } from '../creations/DeleteFilePrompt'
import { LibraryItemRow } from './LibraryItemRow'

import './library.css'

export function LibraryPanel({
  client,
  sessionKey,
  slots,
  running,
  workspaceVersion,
  targetRole,
  onClearTarget,
  useLabel = 'Use in this chat',
  onUseWorkflow,
  onRunAgain,
}: {
  client: AgentdClient | undefined
  /** The chat a "Use" lands in. */
  sessionKey: string
  /** This chat's declared slots, so a reference can be dropped straight into one. */
  slots: Slot[]
  /** A turn is in flight: handing a workflow to the agent waits. */
  running: boolean
  /** Bumped whenever the workspace changes; the catalogue re-reads on it. */
  workspaceVersion: number
  /** A slot waiting for a reference (opened from its From Library button), or ''. */
  targetRole: string
  /** The waiting slot was filled, or the person gave up: back to the workspace. */
  onClearTarget: () => void
  /** The workflow Use button's words — the page says "Use in new chat". */
  useLabel?: string
  onUseWorkflow: (item: LibraryItem) => void
  onRunAgain: (item: LibraryItem) => void
}) {
  const [items, setItems] = useState<LibraryItem[]>([])
  /** The reference cards' own files, for their thumbnails. */
  const [media, setMedia] = useState<Map<string, Artifact>>(() => new Map())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState('')
  const [doomed, setDoomed] = useState<LibraryItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [busy, setBusy] = useState(false)
  const bump = useApp((s) => s.bumpWorkspace)

  const reload = useCallback(async () => {
    if (!client) return
    setLoading(true)
    setError('')
    try {
      const [index, files] = await Promise.all([readIndex(client), referenceFiles(client)])
      setItems(index.items)
      setMedia(files)
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void reload()
  }, [reload, workspaceVersion])

  // A notice is a sentence for a moment, not a state to dismiss.
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const say = useCallback((text: string) => {
    setNotice(text)
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    noticeTimer.current = setTimeout(() => setNotice(''), 4000)
  }, [])

  const addFiles = useCallback(
    async (files: File[]) => {
      if (!client || !files.length) return
      setBusy(true)
      try {
        const added = await uploadToLibrary(client, files)
        say(added.length === 1 ? `Added ${added[0].name} to the Library` : `Added ${added.length} items`)
        await reload()
        bump()
      } catch (e) {
        setError(String((e as Error)?.message || e))
      } finally {
        setBusy(false)
      }
    },
    [client, reload, say, bump],
  )

  const useReference = useCallback(
    async (item: LibraryItem, role: string) => {
      if (!client) return
      setBusy(true)
      try {
        await useReferenceInChat(client, item, sessionKey, role)
        bump()
        if (targetRole && role.replace(/^@/, '') === targetRole) {
          onClearTarget()
          return
        }
        say(role ? `${item.name} now fills @${role.replace(/^@/, '')}` : `${item.name} added to this chat's references`)
      } catch (e) {
        setError(String((e as Error)?.message || e))
      } finally {
        setBusy(false)
      }
    },
    [client, sessionKey, say, bump, targetRole, onClearTarget],
  )

  const doDelete = useCallback(async () => {
    if (!client || !doomed) return
    setDeleting(true)
    setDeleteError('')
    try {
      await deleteItem(client, doomed)
      setDoomed(null)
      await reload()
      bump()
    } catch (e) {
      setDeleteError(String((e as Error)?.message || e))
    } finally {
      setDeleting(false)
    }
  }, [client, doomed, reload, bump])

  const saveNote = useCallback(
    async (item: LibraryItem, note: string) => {
      if (!client) return
      try {
        await updateNote(client, item, note)
        setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, note: note.trim() } : i)))
      } catch (e) {
        setError(String((e as Error)?.message || e))
      }
    },
    [client],
  )

  const uploaded = useMemo(() => items.filter((i) => i.origin === 'uploaded'), [items])
  const saved = useMemo(
    () => items.filter((i) => i.origin === 'saved' && (!filter || i.from?.title === filter)),
    [items, filter],
  )
  const chatsSeen = useMemo(() => {
    const titles = new Map<string, number>()
    for (const i of items) {
      if (i.origin === 'saved' && i.from?.title) titles.set(i.from.title, (titles.get(i.from.title) || 0) + 1)
    }
    return [...titles.entries()]
  }, [items])

  // The drop zone: a real <input type=file> behind a button, plus the whole panel as a target.
  const pickRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const row = (item: LibraryItem) => (
    <LibraryItemRow
      key={item.id}
      item={item}
      media={item.kind === 'reference' ? media.get(item.path) : undefined}
      client={client}
      slots={slots}
      targetRole={targetRole}
      busy={busy || running}
      useLabel={useLabel}
      onUseWorkflow={onUseWorkflow}
      onRunAgain={onRunAgain}
      onUseReference={useReference}
      onDelete={() => setDoomed(item)}
      onNote={saveNote}
    />
  )

  return (
    <div
      className={`lib${over ? ' is-over' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        void addFiles(Array.from(e.dataTransfer.files || []))
      }}
    >
      <div className="lib-head">
        <span className="lib-title">
          <Library size={15} strokeWidth={1.8} /> Library
        </span>
        <span className="lib-count">{items.length}</span>
        <button
          type="button"
          className="lib-upload"
          disabled={!client || busy}
          onClick={() => pickRef.current?.click()}
          title="Add files from this computer"
        >
          <Upload size={13} strokeWidth={1.8} /> {busy ? 'Adding…' : 'Upload'}
        </button>
        <input
          ref={pickRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            void addFiles(Array.from(e.target.files || []))
            e.target.value = ''
          }}
        />
      </div>

      {targetRole && (
        <p className="lib-target">
          Pick a reference for <strong>@{targetRole}</strong> — press Use on one below.{' '}
          <button type="button" className="lib-target-cancel" onClick={onClearTarget}>
            never mind
          </button>
        </p>
      )}
      {notice && <p className="lib-notice">{notice}</p>}
      {error && <p className="lib-error">{error}</p>}

      {!loading && items.length === 0 && (
        <p className="lib-empty">
          Nothing kept yet. Files here are shared by every conversation: a workflow you want to
          run again, a face or a product photo you keep using, a workflow JSON you already have.
          Drop files here, press <em>Save to Library</em> on a workflow, or tick files under All files in the Workspace and press <em>Add to Library</em>.
        </p>
      )}

      {uploaded.length > 0 && (
        <Section title="Uploaded" hint="from this computer" origin="uploaded">
          {uploaded.map(row)}
        </Section>
      )}

      {items.some((i) => i.origin === 'saved') && (
        <Section title="Saved from chats" hint="Add to Library and Save to Library land here" origin="saved">
          {chatsSeen.length > 1 && (
            <div className="lib-filters">
              <button
                type="button"
                className={`lib-chip${!filter ? ' on' : ''}`}
                onClick={() => setFilter('')}
              >
                all
              </button>
              {chatsSeen.map(([title, n]) => (
                <button
                  key={title}
                  type="button"
                  className={`lib-chip${filter === title ? ' on' : ''}`}
                  onClick={() => setFilter(filter === title ? '' : title)}
                  title={`${n} item(s) from this chat`}
                >
                  {title}
                </button>
              ))}
            </div>
          )}
          {saved.map(row)}
        </Section>
      )}

      {doomed && (
        <DeleteFilePrompt
          names={[doomed.name]}
          busy={deleting}
          error={deleteError}
          onDelete={() => void doDelete()}
          onClose={() => setDoomed(null)}
        />
      )}
    </div>
  )
}

function Section({
  title,
  hint,
  origin,
  children,
}: {
  title: string
  hint: string
  origin: LibraryOrigin
  children: React.ReactNode
}) {
  return (
    <section className={`lib-sec lib-sec-${origin}`}>
      <div className="lib-sec-head">
        <span className="lib-sec-title">{title}</span>
        <span className="lib-sec-hint">{hint}</span>
      </div>
      {children}
    </section>
  )
}
