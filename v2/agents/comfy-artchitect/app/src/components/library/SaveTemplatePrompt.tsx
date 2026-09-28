/* "Save as template to reuse" — the one question before a chat becomes a template: its name.
 *
 * Same veil and card as the other prompts (cr-veil / cr-prompt), portalled to <body> so the
 * studio's columns cannot clip it. The name defaults to the chat's title; the description is
 * optional and is what the Library card and the agent's summary lead with.
 *
 * AND ITS SETUP GUIDE. Every node pack and model the template needs goes in with its link
 * (template-setup-guide.ts). What the chat's installer lists could not source is listed here as a
 * field for that link, and the template is not saved with one left empty: a template with a hole
 * is one the agent has to guess its way through later.
 */

import { LayoutTemplate, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  gapKey,
  linkProblem,
  MODEL_KINDS,
  withLinks,
  type SetupGap,
  type SetupGuide,
} from '../../agentd/template-setup-guide'

export function SaveTemplatePrompt({
  defaultName,
  workflowCount,
  busy,
  error,
  setup,
  setupError,
  onSave,
  onClose,
}: {
  defaultName: string
  /** How many workflows go in, said on the button so nothing is a surprise. */
  workflowCount: number
  busy: boolean
  error: string
  /** The chat's setup guide and its gaps; null while it is being worked out. */
  setup: { guide: SetupGuide; gaps: SetupGap[] } | null
  /** Why the guide could not be worked out — the template cannot be saved without one. */
  setupError: string
  onSave: (name: string, description: string, setup: SetupGuide) => void
  onClose: () => void
}) {
  const [name, setName] = useState(defaultName)
  const [description, setDescription] = useState('')
  const [links, setLinks] = useState<Record<string, string>>({})
  const [kinds, setKinds] = useState<Record<string, string>>({})
  const gaps = setup?.gaps || []
  const open = gaps.filter((g) => linkProblem(g, links[gapKey(g)] || '') || (g.type === 'model' && !g.kind && !kinds[gapKey(g)]))

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const ok = name.trim().length > 0 && !busy && !!setup && !open.length
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
          if (ok && setup) onSave(name.trim(), description.trim(), withLinks(setup.guide, gaps, links, kinds))
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
        <div className="tpl-setup">
          <span className="tpl-setup-head">Setup guide</span>
          {setupError ? (
            <p className="tpl-setup-note is-error">{setupError}</p>
          ) : !setup ? (
            <p className="tpl-setup-note">Checking what this template needs…</p>
          ) : !gaps.length ? (
            <p className="tpl-setup-note">
              Every node pack ({setup.guide.node_packs.length}) and model ({setup.guide.models.length}) has its link —
              the template sets itself up and runs as it is.
            </p>
          ) : (
            <>
              <p className="tpl-setup-note">
                These have no link yet. Add each one so the template can set itself up — the setup notes or
                tutorial it came from usually have them.
              </p>
              {gaps.map((g) => {
                const key = gapKey(g)
                const problem = links[key] ? linkProblem(g, links[key]) : ''
                return (
                  <label key={key} className="tpl-gap">
                    <span className="tpl-gap-name">
                      {g.type === 'node_pack' ? `Node pack providing ${g.name}` : g.name}
                      <em>{g.type === 'node_pack' ? 'GitHub repository' : g.kind ? `model · ${g.kind}` : 'model'}</em>
                    </span>
                    <span className="tpl-gap-row">
                      <input
                        value={links[key] || ''}
                        placeholder={g.type === 'node_pack' ? 'https://github.com/owner/repo' : 'https://…/file.safetensors'}
                        onChange={(e) => setLinks((l) => ({ ...l, [key]: e.target.value }))}
                      />
                      {g.type === 'model' && !g.kind && (
                        <select
                          value={kinds[key] || ''}
                          onChange={(e) => setKinds((k) => ({ ...k, [key]: e.target.value }))}
                          aria-label={`Folder for ${g.name}`}
                        >
                          <option value="">folder…</option>
                          {MODEL_KINDS.map((k) => (
                            <option key={k} value={k}>
                              {k}
                            </option>
                          ))}
                        </select>
                      )}
                    </span>
                    {problem && <span className="tpl-gap-bad">{problem}</span>}
                  </label>
                )
              })}
            </>
          )}
        </div>
        <p className={`cr-prompt-note${error ? ' is-error' : ''}`}>
          {error ||
            `Keeps all ${workflowCount} workflow${workflowCount === 1 ? '' : 's'} of this chat, their installers, ` +
              'the inputs they need and a thumbnail — one card in your Library, usable from any new chat.'}
        </p>
        <div className="cr-prompt-actions">
          <button type="submit" className="prime-btn" disabled={!ok}>
            {busy ? 'Saving…' : open.length ? `Add ${open.length} link${open.length === 1 ? '' : 's'} to save` : 'Save template'}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  )
}

export default SaveTemplatePrompt
