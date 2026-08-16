import { useEffect, useRef, useState } from 'react'

/** Pick a folder using the REAL system dialog.
 *
 *  `webkitdirectory` is the only way a sandboxed page opens the OS folder chooser — the window is
 *  remote content with no preload, so Electron's native `dialog` is out of reach, and a hand-drawn
 *  directory browser is a worse imitation of a thing the user already knows how to use.
 *
 *  WHAT COMES BACK IS FILES, NOT A PATH. Electron 32 removed `File.path`, so the page learns the
 *  folder's NAME (from `webkitRelativePath`) and its contents, never its absolute location. That
 *  is why choosing a folder UPLOADS its documents into the workspace rather than scanning them
 *  where they sit: the app genuinely cannot know where they sit. `library_scan` still scans in
 *  place — the agent can do that, because the daemon reads the disk directly.
 *
 *  The set is filtered to documents before anything is queued. A photo folder would otherwise
 *  produce four hundred refusals, one per file, and bury the ones that mattered. */

const DOCUMENTS = /\.(pdf|docx|xlsx|pptx|txt|md|markdown|rst|html?)$/i

export function FolderButton({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[], folder: string, skipped: number) => void
  disabled?: boolean
}) {
  const input = useRef<HTMLInputElement>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    // Not in React's typings, and it must be a real attribute on the element.
    input.current?.setAttribute('webkitdirectory', '')
    input.current?.setAttribute('directory', '')
  }, [])

  return (
    <>
      <button
        className="ghost wide"
        disabled={disabled}
        onClick={() => input.current?.click()}
        title="Open the folder chooser"
      >
        Choose a folder…
      </button>
      {note && <span className="drop-hint">{note}</span>}
      <input
        ref={input}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          const all = Array.from(e.target.files ?? [])
          e.target.value = '' // or choosing the same folder twice fires no change event
          if (!all.length) return

          const docs = all.filter((f) => DOCUMENTS.test(f.name))
          // webkitRelativePath is "<folder>/<...>/<file>" — the first segment is what the user
          // clicked, and the only part of the location this page is allowed to know.
          const folder = String((all[0] as any).webkitRelativePath || '').split('/')[0] || 'folder'
          const skipped = all.length - docs.length

          setNote(
            docs.length
              ? `${folder}: ${docs.length} document${docs.length === 1 ? '' : 's'}` +
                  (skipped ? `, ${skipped} other file${skipped === 1 ? '' : 's'} skipped` : '')
              : `${folder}: no documents in that folder (${all.length} file${
                  all.length === 1 ? '' : 's'
                } checked)`,
          )
          if (docs.length) onFiles(docs, folder, skipped)
        }}
      />
    </>
  )
}
