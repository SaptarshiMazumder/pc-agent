/* The one thing said before a file goes: a workflow that is still running may be using it.
 *
 * NOT A CONFIRMATION IN THE USUAL SENSE. The person clicked delete on a file of their own; the
 * decision is made. What they may not know is that a run in another chat can still be reading a
 * reference or writing beside an output, and deleting under it produces an error in that chat
 * with no visible cause. So this says that once, plainly, and deletes on the next click.
 *
 * PORTALLED TO <body> for the same reason the lightbox is: the studio's columns clip and stack
 * anything drawn inside them.
 */

import { Trash2, X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

export function DeleteFilePrompt({
  names,
  busy,
  error,
  onDelete,
  onClose,
}: {
  /** What is about to go — one file, or a workflow's two. */
  names: string[]
  busy: boolean
  /** The daemon refused: shown in place of the warning, with the same two buttons. */
  error?: string
  onDelete: () => void
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  return createPortal(
    <div className="cr-veil" onClick={busy ? undefined : onClose}>
      <div
        className="modal cr-prompt"
        role="dialog"
        aria-modal="true"
        aria-label="Delete file"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cr-prompt-head">
          <span className="cr-prompt-title">
            Delete {names.length === 1 ? names[0] : `${names.length} files`}
          </span>
          <button className="fv-btn" onClick={onClose} disabled={busy} title="Close" aria-label="Close">
            <X size={15} strokeWidth={1.8} />
          </button>
        </div>
        {names.length > 1 && (
          <ul className="cr-prompt-list">
            {names.map((n) => (
              <li key={n} className="st-mono">
                {n}
              </li>
            ))}
          </ul>
        )}
        <p className={`cr-prompt-note${error ? ' is-error' : ''}`}>
          {error ||
            'Deleting files can break workflows that use them.'}
        </p>
        <div className="cr-prompt-actions">
          <button className="cr-btn-danger" onClick={onDelete} disabled={busy}>
            <Trash2 size={14} strokeWidth={1.8} />
            {busy ? 'Deleting…' : error ? 'Try again' : 'Delete'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
