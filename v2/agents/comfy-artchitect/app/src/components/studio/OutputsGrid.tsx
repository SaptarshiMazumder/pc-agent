/* The Workspace's Outputs section — what this chat has MADE, as pictures rather than as names.
 *
 * WHY PICTURES. The file tree lists renders beside workflow JSON, installers and manifests, all
 * as names. That is right for somebody auditing the folder and wrong for the question most people
 * open the stage to answer: "what did it make, and is it good?" So renders get a grid of their
 * own, and the tree stays, unchanged, in the All files section below it.
 *
 * THE THUMBNAIL RULE STILL HOLDS. The grid draws bounded `thumbnailUrl` previews; the original
 * bytes are fetched only when a tile is opened, into the same FileViewer every file uses.
 *
 * EVERY ACTION HERE IS ONE THE TREE ALREADY HAD, on the same handlers: Add to Library copies into
 * the shared Library, Delete asks once and the daemon removes it (refused mid-run with the same
 * reason the tree gives), Download is the file itself.
 */

import { BookmarkPlus, Check, Download, Image as ImageIcon, Loader2, Music, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { fileUrl, thumbnailUrl, type Artifact } from '../../agentd/artifacts'
import type { LibrarySaveOutcome } from '../../agentd/library'
import { saveLabel, useSaveFeedback } from '../library/use-save-feedback'
import { DeleteFilePrompt } from '../creations/DeleteFilePrompt'
import { MediaKindTag } from '../media/MediaKindTag'

function OutputThumb({ file }: { file: Artifact }) {
  const [failed, setFailed] = useState(false)
  if (file.kind === 'image' && !failed) {
    return <img src={thumbnailUrl(file.path)} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
  }
  if (file.kind === 'video') {
    // `metadata` draws the first frame without pulling the clip.
    return <video src={fileUrl(file.path)} muted preload="metadata" playsInline />
  }
  return (
    <span className="op-thumb-blank" aria-hidden="true">
      {file.kind === 'audio' ? <Music size={20} /> : <ImageIcon size={20} />}
    </span>
  )
}

export function OutputsGrid({
  outputs,
  selectedPath,
  onOpen,
  onDelete,
  onAddToLibrary,
  deletionDisabled = '',
}: {
  /** This chat's renders, in the order they arrived. */
  outputs: Artifact[]
  /** The file open in the viewer beside the Workspace, to ring its tile. */
  selectedPath: string
  onOpen: (a: Artifact) => void
  onDelete?: (paths: string[]) => Promise<void>
  onAddToLibrary?: (paths: string[]) => Promise<LibrarySaveOutcome>
  deletionDisabled?: string
}) {
  const [doomed, setDoomed] = useState<Artifact | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [notice, setNotice] = useState('')
  const feedback = useSaveFeedback()

  const save = async (a: Artifact): Promise<void> => {
    if (!onAddToLibrary) return
    try {
      setNotice((await feedback.run(a.path, () => onAddToLibrary([a.path]))).message)
    } catch (e) {
      setNotice(`Could not add to the Library: ${String((e as Error)?.message || e)}`)
    } finally {
      setTimeout(() => setNotice(''), 4000)
    }
  }
  const doDelete = async (): Promise<void> => {
    if (!doomed || !onDelete) return
    setDeleting(true)
    setDeleteError('')
    try {
      await onDelete([doomed.path])
      setDoomed(null)
    } catch (e) {
      setDeleteError(String((e as Error)?.message || e))
    } finally {
      setDeleting(false)
    }
  }

  if (!outputs.length) {
    return <p className="ws-empty">Images and videos land here the moment a run finishes.</p>
  }

  // Newest first: the render that just landed is the one being judged.
  const wall = [...outputs].reverse()

  return (
    <>
      {notice && <p className="op-note">{notice}</p>}
      <div className="op-wall">
        {wall.map((a) => (
          <figure key={a.path} className={`op-tile${a.path === selectedPath ? ' is-on' : ''}`}>
            <button type="button" className="op-tile-media" onClick={() => onOpen(a)} title={`Open ${a.name}`}>
              <OutputThumb file={a} />
            </button>
            {(a.kind === 'video' || a.kind === 'image') && (
              <span className="op-tile-tag">
                <MediaKindTag kind={a.kind} over />
              </span>
            )}
            <span className="op-tile-acts">
              <a className="op-act" href={fileUrl(a.path)} download={a.name} title="Download" aria-label={`Download ${a.name}`}>
                <Download size={14} strokeWidth={1.9} />
              </a>
              {onAddToLibrary && (
                <button
                  type="button"
                  className="op-act"
                  disabled={feedback.stateOf(a.path) === 'saving'}
                  onClick={() => void save(a)}
                  title={saveLabel(feedback.stateOf(a.path), 'Add to Library, shared by every conversation')}
                  aria-label={`Add ${a.name} to the Library`}
                >
                  {feedback.stateOf(a.path) === 'saving' ? (
                    <Loader2 size={14} strokeWidth={1.9} className="ld-spin" />
                  ) : feedback.stateOf(a.path) === 'idle' ? (
                    <BookmarkPlus size={14} strokeWidth={1.9} />
                  ) : (
                    <Check size={14} strokeWidth={2.2} />
                  )}
                </button>
              )}
              {onDelete && (
                <button
                  type="button"
                  className="op-act is-danger"
                  disabled={!!deletionDisabled}
                  onClick={() => {
                    setDeleteError('')
                    setDoomed(a)
                  }}
                  title={deletionDisabled || 'Delete this render'}
                  aria-label={`Delete ${a.name}`}
                >
                  <Trash2 size={14} strokeWidth={1.9} />
                </button>
              )}
            </span>
            <figcaption className="op-tile-name st-mono">{a.name}</figcaption>
          </figure>
        ))}
      </div>

      {doomed && (
        <DeleteFilePrompt
          names={[doomed.name]}
          busy={deleting}
          error={deleteError}
          onDelete={() => void doDelete()}
          onClose={() => setDoomed(null)}
        />
      )}
    </>
  )
}

export default OutputsGrid
