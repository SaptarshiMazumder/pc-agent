/* "About this template" — the template's about.json, read in full, over the page.
 *
 * Opened from a Library template card and a Templates page card. Same veil and card as the other
 * prompts (cr-veil / cr-prompt), portalled to <body> so no column clips it. Sections appear only
 * when the about has them; a template saved before abouts has none and shows no button at all.
 */

import { Info, X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

import type { TemplateAbout } from '../../agentd/template-about'

const NEEDS: [keyof TemplateAbout['needs'], string][] = [
  ['runs_on', 'Runs on'],
  ['credits', 'Credits'],
  ['vram', 'GPU memory'],
  ['time', 'Time'],
]

export function TemplateAboutPanel({
  name,
  about,
  onClose,
}: {
  name: string
  about: TemplateAbout
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const needs = NEEDS.filter(([k]) => about.needs[k])
  return createPortal(
    <div className="cr-veil" onClick={onClose}>
      <div
        className="modal cr-prompt ta-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`About ${name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cr-prompt-head">
          <span className="cr-prompt-title">
            <Info size={15} strokeWidth={1.8} /> {name}
          </span>
          <button type="button" className="fv-btn" onClick={onClose} title="Close" aria-label="Close">
            <X size={15} strokeWidth={1.8} />
          </button>
        </div>
        <div className="ta-body">
          <p className="ta-makes">{about.makes}</p>
          <section>
            <h4>How it works</h4>
            <p>{about.how_it_works}</p>
          </section>
          {about.inputs.length > 0 && (
            <section>
              <h4>What you add</h4>
              <ul>
                {about.inputs.map((i) => (
                  <li key={i.role}>
                    <code>@{i.role}</code> — {i.what}
                    {i.tips && <span className="ta-tip">{i.tips}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {about.you_can_change.length > 0 && (
            <section>
              <h4>What you can change</h4>
              <ul>
                {about.you_can_change.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </section>
          )}
          {about.example && (
            <section>
              <h4>Its example</h4>
              <p className="ta-example">{about.example.prompt}</p>
              {about.example.result && <p>{about.example.result}</p>}
            </section>
          )}
          {needs.length > 0 && (
            <section>
              <h4>What it needs</h4>
              <dl className="ta-needs">
                {needs.map(([k, label]) => (
                  <div key={k}>
                    <dt>{label}</dt>
                    <dd>{about.needs[k]}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          {about.limits.length > 0 && (
            <section>
              <h4>Good to know</h4>
              <ul>
                {about.limits.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default TemplateAboutPanel
