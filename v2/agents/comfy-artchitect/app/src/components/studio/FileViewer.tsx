/* One artifact, open in the middle of the studio — the pane the whole layout is built around.
 *
 * WHY THIS IS NOT A MODAL. As an overlay it was a detour: you covered the workspace to read a file,
 * then dismissed it to get back. But looking at the file IS the work here — checking the checkpoint,
 * counting nodes, seeing the render — so it earns the biggest surface on screen rather than a layer
 * above it. The tree on the left picks; this shows.
 *
 * RENDERED BY WHAT IT IS. JSON gets line numbers and token colour, because every conversation about
 * a graph is "look at node 105". Markdown gets a Raw/Readable switch, defaulting to RAW: these
 * READMEs exist to carry exact model filenames and download URLs, and a renderer that reflows them
 * into prose is working against the reason you opened one. Media plays inline; anything binary
 * offers the download instead of pretending to show it.
 *
 * COPY IS A BUTTON. "You can select it" is not a feature when the thing to select is 8 KB of JSON
 * in a scrolling pane.
 */

import { Check, Copy, Download, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { fileUrl, humanSize, type Artifact } from '../../agentd/artifacts'
import Markdown from '../Markdown'
import { CodeBlock } from './CodeBlock'
import { isCanvasImportable, setDragPayload } from './dragOut'

/** Text we are willing to render ourselves, by extension. Anything else is offered as a file. */
const TEXTUAL = /\.(json|txt|md|ya?ml|csv|log|py|js|ts|tsx|css|html|xml|toml|ini|sh)$/i

export function FileViewer({ file, onClose }: { file: Artifact; onClose?: () => void }) {
  const href = fileUrl(file.path)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [readable, setReadable] = useState(false)
  const [copied, setCopied] = useState(false)

  const textual = file.kind === 'file' && TEXTUAL.test(file.name)
  const isJson = /\.json$/i.test(file.name)
  const isMarkdown = /\.md$/i.test(file.name)

  useEffect(() => {
    // Switching files must not show the previous one's contents for a frame, nor keep its view mode.
    setText(null)
    setError('')
    setReadable(false)
    if (!textual) return
    let alive = true
    void fetch(href)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((raw) => {
        if (!alive) return
        // Pretty-print JSON; keep the raw text when it will not parse, because a malformed
        // workflow is exactly the file you most need to look at.
        if (isJson) {
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
  }, [href, isJson, textual])

  const copy = (): void => {
    if (text == null) return
    void navigator.clipboard?.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  return (
    <div className="fv">
      {/* STICKY, because a 400-line graph scrolls and the name of what you are reading should not
          scroll away with it. */}
      <header className="fv-head">
        {/* DRAGGABLE BY ITS NAME. Drag this onto a ComfyUI tab and the canvas receives the file —
            see dragOut.ts for why that needs `DownloadURL` and not just a link. */}
        <span
          className="fv-name st-mono"
          draggable
          onDragStart={(e) => setDragPayload(e.dataTransfer, file)}
          title={
            isCanvasImportable(file)
              ? `${file.name} — drag onto your ComfyUI tab to load it`
              : file.name
          }
        >
          {file.name}
        </span>
        <span className="fv-sub">
          {file.kind}
          {file.size ? ` · ${humanSize(file.size)}` : ''}
        </span>

        {isMarkdown && text != null && (
          <div className="fv-modes">
            <button className={readable ? '' : 'is-on'} onClick={() => setReadable(false)}>
              Raw
            </button>
            <button className={readable ? 'is-on' : ''} onClick={() => setReadable(true)}>
              Readable
            </button>
          </div>
        )}

        {textual && text != null && (
          <button className="fv-btn" onClick={copy} title="Copy the whole file">
            {copied ? <Check size={15} strokeWidth={2} /> : <Copy size={15} strokeWidth={1.8} />}
          </button>
        )}
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
          ) : isMarkdown && readable ? (
            <div className="fv-readable">
              <Markdown text={text} />
            </div>
          ) : (
            <CodeBlock text={text} language={isJson ? 'json' : 'text'} />
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
