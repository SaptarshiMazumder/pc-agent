/* "Save as template to reuse" — the one question before a chat becomes a template: its name.
 *
 * Same veil and card as the other prompts (cr-veil / cr-prompt), portalled to <body> so the
 * studio's columns cannot clip it. The name defaults to the chat's title; the description is
 * optional and is what the Library card and the agent's summary lead with.
 */

import { LayoutTemplate, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

export function SaveTemplatePrompt({
  defaultName,
  workflowCount,
  busy,
  error,
  onSave,
  onClose,
}: {
  defaultName: string
  /** How many workflows go in, said on the button so nothing is a surprise. */
  workflowCount: number
  busy: boolean
  error: string
  onSave: (name: string, description: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState(defaultName)
  const [description, setDescription] = useState('')

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const ok = name.trim().length > 0 && !busy
  return createPortal(
    <div className="cr-veil" onClick={busy ? undefined : onClose}>
      <form
        className="modal cr-prompt tpl-prompt"
        role="dialog"
        aria-modal="true"
        aria-label="Save as template"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) onSave(name.trim(), description.trim())
        }}
      >
        <div className="cr-prompt-head">
          <span className="cr-prompt-title">
            <LayoutTemplate size={15} strokeWidth={1.8} /> Save as template to reuse
          </span>
          <button type="button" className="fv-btn" onClick={onClose} disabled={busy} title="Close" aria-label="Close">
            <X size={15} strokeWidth={1.8} />
          </button>
        </div>
        <label className="tpl-field">
          <span>Template name</span>
          <input value={name} autoFocus maxLength={80} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="tpl-field">
          <span>What it makes (optional)</span>
          <textarea
            value={description}
            rows={2}
            maxLength={300}
            placeholder="e.g. Two characters fight in a temple, 7 s vertical video"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <p className={`cr-prompt-note${error ? ' is-error' : ''}`}>
          {error ||
            `Keeps all ${workflowCount} workflow${workflowCount === 1 ? '' : 's'} of this chat, their installers, ` +
              'the inputs they need and a thumbnail — one card in your Library, usable from any new chat.'}
        </p>
        <div className="cr-prompt-actions">
          <button type="submit" className="prime-btn" disabled={!ok}>
            {busy ? 'Saving…' : 'Save template'}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  )
}

export default SaveTemplatePrompt
