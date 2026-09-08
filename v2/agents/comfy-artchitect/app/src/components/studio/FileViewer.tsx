/* One artifact, open in the middle of the studio — the pane the whole layout is built around.
 *
 * WHY THIS IS NOT A MODAL ANY MORE. As an overlay it was a detour: you covered the workspace to
 * read a file, then dismissed it to get back. But looking at the file IS the work here — checking
 * the checkpoint, counting nodes, seeing the render — so it earns the biggest surface on screen
 * rather than a layer above it. The tree on the left picks; this shows. Nothing to dismiss,
 * nothing hidden behind it.
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

export function FileViewer({ file, onClose }: { file: Artifact; onClose?: () => void }) {
  const href = fileUrl(file.path)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')

  const textual = file.kind === 'file' && TEXTUAL.test(file.name)

  useEffect(() => {
    // Switching files must not show the previous one's contents for a frame.
    setText(null)
    setError('')
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
    <div className="fv">
      <header className="fv-head">
        <span className="fv-name st-mono">{file.name}</span>
        <span className="fv-sub">
          {file.kind}
          {file.size ? ` · ${humanSize(file.size)}` : ''}
        </span>
        <a className="fv-btn" href={href} download={file.name} title="Download this file">
          <Download size={15} strokeWidth={1.8} />
        </a>
        {onClose && (
          <button className="fv-btn" onClick={onClose} title="Close" aria-label="Close preview">
            <X size={16} strokeWidth={1.8} />
          </button>
        )}
      </header>

      <div className="fv-body">
        {file.kind === 'image' && <img className="fv-media" src={href} alt={file.name} />}
        {file.kind === 'video' && <video className="fv-media" src={href} controls autoPlay loop />}
        {file.kind === 'audio' && <audio className="fv-audio" src={href} controls />}
        {textual &&
          (error ? (
            <p className="fv-error">could not read this file: {error}</p>
          ) : text === null ? (
            <p className="fv-loading">reading…</p>
          ) : (
            <pre className="fv-text">{text}</pre>
          ))}
        {file.kind === 'file' && !textual && (
          <p className="fv-loading">
            This file cannot be shown here — <a href={href} download={file.name}>download it</a>.
          </p>
        )}
      </div>
    </div>
  )
}
