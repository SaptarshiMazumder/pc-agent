/* The Gallery (formerly My creations) — every render and every workflow this account has made,
 * filed by the chat that made it.
 *
 * RENDERS FIRST, since the creative-studio redesign: a gallery is where you go to look at what
 * you made, so it opens on the pictures. The Workflows shelf is one click away, unchanged.
 *
 * WHY THE CONVERSATION IS NOT ENOUGH. A workflow is built by iterating — emit, run, read the
 * server's complaint, change one thing, emit again — and a render is judged by looking at it next
 * to the last one. Six chats in, the thing you want is in one of them, and the transcript is the
 * slowest way to find out which. This screen is the library: one section per chat, newest first,
 * and inside it either the workflows (each as its two files) or the renders (as a wall of
 * thumbnails).
 *
 * IT INVENTS NOTHING. Sections come from the folders on disk (agentd/chat-library.ts), so an empty
 * screen is a true statement, and a chat that was deleted keeps its files under "Deleted
 * conversations" because deleting a chat never deleted its folder.
 *
 * DELETE IS DIRECT. One click, one note about running workflows, gone. The files are the user's
 * own and the decision is theirs; the screen's job is to make the consequence visible, not to
 * argue. The removal goes through the daemon's own workspace delete — the same door the
 * reference Replace flow uses — and the list is re-read afterwards, so what is shown is what is
 * on disk and never what the window remembers.
 */

import './creations.css'

import { BookmarkPlus, FileJson, Image as ImageIcon, MessageSquare, Play, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { MediaKindTag } from '../media/MediaKindTag'

import type { AgentdClient } from '@agentd/client'

import { fileUrl, humanSize, thumbnailUrl } from '../../agentd/artifacts'
import {
  deleteLibraryFiles,
  useChatLibrary,
  type ChatGroup,
  type LibraryFile,
} from '../../agentd/chat-library'
import type { ChatRow } from '../../agentd/sessions'
import { ImageLightbox } from '../studio/ImageLightbox'
import { collectWorkflows, WorkflowCard, workflowFiles } from '../workflows/WorkflowCard'
import { DeleteFilePrompt } from './DeleteFilePrompt'
import { saveFromChat } from '../../agentd/library'
import { useApp } from '../../state/store'

type Shelf = 'workflows' | 'outputs'

/** "today, 11:59 PM" / "Tue 15 Sep" — a date you can place, unlike the rail's bare weekday. */
function madeOn(ts: number): string {
  const d = new Date(ts * 1000)
  const sameDay = d.toDateString() === new Date().toDateString()
  if (sameDay) return `today, ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
}

/** What a section counts: workflows are pairs of files, so "4 files" would be "2 workflows". */
function countLabel(shelf: Shelf, files: LibraryFile[]): string {
  const n = shelf === 'workflows' ? collectWorkflows(files).length : files.length
  const noun = shelf === 'workflows' ? 'workflow' : 'render'
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

/** The header of one chat's section. A live chat's header opens that chat — the files are the
 *  reason to go back to it — and the orphan section has nowhere to go. */
function SectionHead({
  group,
  shelf,
  onOpen,
}: {
  group: ChatGroup
  shelf: Shelf
  onOpen?: () => void
}) {
  const meta = `${countLabel(shelf, group.files)}${group.modified ? ` · ${madeOn(group.modified)}` : ''}`
  const inner = (
    <>
      <span className="cr-sec-ico">
        <MessageSquare size={13} strokeWidth={1.8} />
      </span>
      <span className="cr-sec-title">{group.title}</span>
      <span className="cr-sec-meta">{meta}</span>
    </>
  )
  return onOpen ? (
    <button className="cr-sec-head is-link" onClick={onOpen} title="Open this conversation">
      {inner}
    </button>
  ) : (
    <div className="cr-sec-head">{inner}</div>
  )
}

/** One render. An image draws the daemon's bounded thumbnail (the original is fetched only when
 *  the tile is opened, like everywhere else) and falls back to the file itself if the thumbnail
 *  service refuses; a video draws its first frame. */
function RenderTile({
  file,
  onOpen,
  onDelete,
  onSave,
}: {
  file: LibraryFile
  onOpen: () => void
  onDelete: () => void
  /** Copy this render into the Library, where every chat can reach it. */
  onSave: () => void
}) {
  const src = fileUrl(file.path)
  const [thumbFailed, setThumbFailed] = useState(false)
  return (
    <figure className="cr-tile">
      <button className="cr-tile-media" onClick={onOpen} title={file.name}>
        {file.kind === 'video' ? (
          <video src={src} muted preload="metadata" />
        ) : file.kind === 'image' ? (
          <img
            src={thumbFailed ? src : thumbnailUrl(file.path)}
            alt={file.name}
            loading="lazy"
            decoding="async"
            onError={() => setThumbFailed(true)}
          />
        ) : (
          <span className="cr-tile-blank">
            <FileJson size={18} strokeWidth={1.6} />
          </span>
        )}
        {file.kind === 'video' && (
          <span className="cr-tile-badge">
            <Play size={11} strokeWidth={2} />
          </span>
        )}
        {(file.kind === 'video' || file.kind === 'image') && (
          <span className="cr-tile-kind">
            <MediaKindTag kind={file.kind} over />
          </span>
        )}
      </button>
      <figcaption className="cr-tile-cap">
        <span className="cr-tile-name st-mono">{file.name}</span>
        <span className="cr-tile-size">{humanSize(file.size || 0)}</span>
        <button
          className="cr-tile-save"
          onClick={onSave}
          title="Save to Library"
          aria-label={`Save ${file.name} to the Library`}
        >
          <BookmarkPlus size={13} strokeWidth={1.8} />
        </button>
        <button
          className="cr-tile-del"
          onClick={onDelete}
          title="Delete this render"
          aria-label={`Delete ${file.name}`}
        >
          <Trash2 size={13} strokeWidth={1.8} />
        </button>
      </figcaption>
    </figure>
  )
}

export default function Gallery({
  client,
  chats,
  workspaceVersion,
  onOpenChat,
}: {
  client: AgentdClient | undefined
  chats: ChatRow[]
  workspaceVersion: number
  onOpenChat: (sessionId: string) => void
}) {
  const [shelf, setShelf] = useState<Shelf>('outputs')
  const { groups, loading, reload } = useChatLibrary(client, shelf, chats, workspaceVersion)

  const [viewing, setViewing] = useState<LibraryFile | null>(null)
  const [doomed, setDoomed] = useState<LibraryFile[] | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')

  const askDelete = useCallback((files: LibraryFile[]) => {
    setDeleteError('')
    setDoomed(files)
  }, [])
  /* SAVE TO LIBRARY, from the card or the tile: a copy on the daemon's disk into the shared
     folder, captioned with the chat that made it. The sentence it answers shows under the title
     for a moment — a save is a fact, not a state to dismiss. */
  const [notice, setNotice] = useState('')
  const saveToLibrary = useCallback(
    async (files: LibraryFile[], g: ChatGroup) => {
      if (!client) return
      try {
        const added = await saveFromChat(
          client,
          files.map((f) => ({ rel: f.rel, name: f.name, kind: f.kind, path: f.path })),
          { chat: g.sessionId || g.folder, title: g.title },
        )
        setNotice(added.length === 1 ? `Saved ${added[0].name} to the Library` : `Saved ${added.length} items to the Library`)
        useApp.getState().bumpWorkspace()
      } catch (e) {
        setNotice(`Could not save: ${String((e as Error)?.message || e)}`)
      }
      setTimeout(() => setNotice(''), 4000)
    },
    [client],
  )
  const doDelete = useCallback(async () => {
    if (!client || !doomed) return
    setDeleting(true)
    try {
      await deleteLibraryFiles(client, doomed)
      setDoomed(null)
      reload()
    } catch (e) {
      setDeleteError(String((e as Error)?.message || e) || 'the daemon refused')
    } finally {
      setDeleting(false)
    }
  }, [client, doomed, reload])

  const total = useMemo(() => groups.reduce((n, g) => n + g.files.length, 0), [groups])
  const sub = loading
    ? 'Reading your folders…'
    : total === 0
      ? shelf === 'workflows'
        ? 'No workflows yet'
        : 'No renders yet'
      : `${groups.length} conversation${groups.length === 1 ? '' : 's'} · newest first`

  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">Gallery</h1>
          <p className="page-sub">{notice || sub}</p>
        </div>
        {/* TWO SHELVES, ONE SCREEN. Workflows and renders are made by the same chats and are
            wanted for the same reason — "which chat made that?" — so they share the sections
            and differ only in how a file is drawn. */}
        <div className="cr-seg" role="tablist">
          <button
            role="tab"
            aria-selected={shelf === 'outputs'}
            className={`cr-seg-btn${shelf === 'outputs' ? ' on' : ''}`}
            onClick={() => setShelf('outputs')}
          >
            <ImageIcon size={13} strokeWidth={1.8} /> Images & videos
          </button>
          <button
            role="tab"
            aria-selected={shelf === 'workflows'}
            className={`cr-seg-btn${shelf === 'workflows' ? ' on' : ''}`}
            onClick={() => setShelf('workflows')}
          >
            <FileJson size={13} strokeWidth={1.8} /> Workflows
          </button>
        </div>
      </header>

      {/* ONE COLUMN. The shared stage keeps a column free for the conversation's aside; a
          library has no aside, and leaving the column empty squeezed every card into the left
          half of a wide window. */}
      <div className="stage cr-stage">
        <div className="stage-main cr-scroll">
          {!loading && total === 0 ? (
            <p className="cr-empty">
              {shelf === 'workflows'
                ? 'Ask for anything in a chat — a product shot, a talking clip, a whole shoot. The workflow behind each result is saved twice, once to run and once to import into ComfyUI, and both land here under the chat that made them.'
                : 'Nothing made yet. Every image and clip a chat produces lands here, filed under the chat that made it.'}
            </p>
          ) : (
            groups.map((g) => (
              <section key={g.folder || '__orphans'} className="cr-sec">
                <SectionHead
                  group={g}
                  shelf={shelf}
                  onOpen={g.sessionId ? () => onOpenChat(g.sessionId!) : undefined}
                />
                {shelf === 'workflows' ? (
                  <div className="wf-shelf">
                    {collectWorkflows(g.files).map((wf) => (
                      <WorkflowCard
                        key={wf.name}
                        wf={wf}
                        onSave={(w) => void saveToLibrary(workflowFiles(w), g)}
                        onDelete={(w) => askDelete(workflowFiles(w))}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="cr-grid">
                    {g.files.map((f) => (
                      <RenderTile
                        key={f.rel || f.path}
                        file={f}
                        onOpen={() => setViewing(f)}
                        onDelete={() => askDelete([f])}
                        onSave={() => void saveToLibrary([f], g)}
                      />
                    ))}
                  </div>
                )}
              </section>
            ))
          )}
        </div>
      </div>

      {viewing && (viewing.kind === 'image' || viewing.kind === 'video') && (
        <ImageLightbox
          src={fileUrl(viewing.path)}
          name={viewing.name}
          video={viewing.kind === 'video'}
          onClose={() => setViewing(null)}
        />
      )}
      {doomed && (
        <DeleteFilePrompt
          names={doomed.map((f) => f.name)}
          busy={deleting}
          error={deleteError}
          onDelete={() => void doDelete()}
          onClose={() => setDoomed(null)}
        />
      )}
    </>
  )
}
