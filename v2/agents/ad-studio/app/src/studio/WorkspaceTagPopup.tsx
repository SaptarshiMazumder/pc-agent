/* The box under the files selected in the workspace: one text box, pre-filled with their names, for
 * the user to edit into whatever they want to say about them ("this is the image I want a video
 * from"). "Add to chat" puts it into the chat box. */

import { Tag, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { WorkspaceFile } from '../state/store'

/** What the box starts with: the files' names, one per line. */
function prefill(files: WorkspaceFile[]): string {
  return files.map((f) => f.path.split('/').pop() || f.path).join('\n')
}

export function WorkspaceTagPopup({
  files,
  onTag,
  onRemove,
  onClose,
}: {
  /** The selected files. */
  files: WorkspaceFile[]
  onTag: (tag: string) => void
  onRemove: () => void
  onClose: () => void
}) {
  const key = files.map((f) => f.path).join('|')
  const [text, setText] = useState(prefill(files))
  const box = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    setText(prefill(files))
    box.current?.focus()
    box.current?.select()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return (
    <div className="ws-tag-popup" role="dialog" aria-label="Say what these files are">
      <header className="ws-tag-head">
        <span className="strip-label">
          <Tag size={12} /> {files.length} selected — what {files.length > 1 ? 'are they' : 'is it'}?
        </span>
        <button className="ref-x" onClick={onClose} title="Done">
          <X size={13} />
        </button>
      </header>
      <textarea
        ref={box}
        className="ws-tag-text"
        rows={Math.min(6, Math.max(2, text.split('\n').length))}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) onTag(text.trim())
        }}
      />
      <div className="ws-tag-actions">
        <button className="ref-add" onClick={onRemove} title="Take them out of this ad's workspace (they stay in your library)">
          <Trash2 size={11} /> Remove from workspace
        </button>
        <button className="prime-btn" onClick={() => onTag(text.trim())} title="Put it in the chat box (Ctrl+Enter)">
          Add to chat
        </button>
      </div>
    </div>
  )
}
