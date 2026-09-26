/* Why a pasted image did not attach: it is over the chat cap, and it belongs in Inputs.
 *
 * A POPUP, NOT A THREAD LINE. The refusal used to be a red system message in the transcript,
 * easy to scroll past and gone from view by the next turn — and the person pasting a photo is
 * usually about to ask for a render FROM it, which the chat cannot do anyway. So this stops
 * them once, says the cap and names the right door.
 *
 * Same veil and card as the delete prompt (cr-veil / cr-prompt), portalled to <body> so the
 * studio's columns cannot clip it.
 */

import { ImageOff, X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

import { MAX_CHAT_IMAGE_LABEL } from '../agentd/chat'

const mb = (b: number): string => `${(b / (1024 * 1024)).toFixed(1)} MB`

export function ChatImageTooBigPrompt({
  files,
  onClose,
}: {
  files: { name: string; size: number }[]
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const one = files.length === 1
  return createPortal(
    <div className="cr-veil" onClick={onClose}>
      <div
        className="modal cr-prompt"
        role="dialog"
        aria-modal="true"
        aria-label="Image too large for the chat"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cr-prompt-head">
          <span className="cr-prompt-title">
            <ImageOff size={15} strokeWidth={1.8} /> {one ? 'Image' : 'Images'} too large for the chat
          </span>
          <button className="fv-btn" onClick={onClose} title="Close" aria-label="Close">
            <X size={15} strokeWidth={1.8} />
          </button>
        </div>
        <ul className="cr-prompt-list">
          {files.map((f) => (
            <li key={f.name + f.size} className="st-mono">
              {f.name} · {mb(f.size)}
            </li>
          ))}
        </ul>
        <p className="cr-prompt-note">
          Chat images are capped at {MAX_CHAT_IMAGE_LABEL} — the agent only looks at them. To use{' '}
          {one ? 'this image' : 'these images'} as a reference for generation, add{' '}
          {one ? 'it' : 'them'} on the <b>Inputs</b> tab of the Workspace.
        </p>
        <div className="cr-prompt-actions">
          <button className="prime-btn" onClick={onClose} autoFocus>
            Got it
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
