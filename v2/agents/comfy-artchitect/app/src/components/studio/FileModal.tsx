/* One artifact, opened — its NAME at the top and its CONTENT below.
 *
 * WHY A MODAL AND NOT A LINK. A card that opens the file in a browser tab hands a workflow's JSON
 * to the raw viewer: a wall of unindented text in a tab with no name on it, which is what made the
 * artifacts panel unreadable. What someone wants from a file called `x.api.json` is to LOOK at it —
 * confirm the checkpoint, count the nodes, check a seed — and then come back. That is a modal.
 *
 * RENDERED BY WHAT IT IS. JSON is parsed and re-printed indented (falling back to raw text if it
 * does not parse — an honest view of a broken file beats an error); media plays inline; anything
 * binary offers the download instead of pretending to show it.
 */

import { Download, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { fileUrl, humanSize, type Artifact } from '../../agentd/artifacts'

/** Text we are willing to render in a <pre>, by extension. Anything else is offered as a file. */
const TEXTUAL = /\.(json|txt|md|ya?ml|csv|log|py|js|ts|tsx|css|html|xml|toml|ini|sh)$/i

export function FileModal({ file, onClose }: { file: Artifact; onClose: () => void }) {
  const href = fileUrl(file.path)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')

  // Escape closes, wherever focus is — the reflex everyone already has.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const textual = file.kind === 'file' && TEXTUAL.test(file.name)

  useEffect(() => {
    if (!textual) return
    let alive = true
    void fetch(href)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((raw) => {
        if (!alive) return
        // Pretty-print JSON; keep the raw text when it will not parse, because a malformed
        // workflow is exactly the file you most need to look at.
        if (/\.json$/i.test(file.name)) {
          try {
            setText(JSON.stringify(JSON.parse(raw), null, 2))
            return
          } catch {
            /* fall through to raw */
          }
        }
        setText(raw)
      })
      .catch((e) => alive && setError(String((e as Error)?.message || e)))
    return () => {
      alive = false
    }
  }, [href, file.name, textual])

  return (
    <div className="fm-backdrop" onClick={onClose}>
      {/* The panel swallows its own clicks so a click INSIDE never closes what you just opened. */}
      <div className="fm-panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <header className="fm-head">
          <div className="fm-titles">
            <span className="fm-name st-mono">{file.name}</span>
            <span className="fm-sub">
              {file.kind}
              {file.size ? ` · ${humanSize(file.size)}` : ''}
            </span>
          </div>
          <a className="fm-btn" href={href} download={file.name} title="Download this file">
            <Download size={15} strokeWidth={1.8} />
          </a>
          <button className="fm-btn" onClick={onClose} title="Close (Esc)">
            <X size={16} strokeWidth={1.8} />
          </button>
        </header>

        <div className="fm-body">
          {file.kind === 'image' && <img className="fm-media" src={href} alt={file.name} />}
          {file.kind === 'video' && <video className="fm-media" src={href} controls autoPlay loop />}
          {file.kind === 'audio' && <audio className="fm-audio" src={href} controls />}
          {textual &&
            (error ? (
              <p className="fm-error">could not read this file: {error}</p>
            ) : text === null ? (
              <p className="fm-loading">reading…</p>
            ) : (
              <pre className="fm-text">{text}</pre>
            ))}
          {file.kind === 'file' && !textual && (
            <p className="fm-loading">
              This file cannot be shown here — <a href={href} download={file.name}>download it</a>.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
