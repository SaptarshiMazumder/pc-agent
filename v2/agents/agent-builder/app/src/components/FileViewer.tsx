/* One file, opened from the tree. Media plays, markdown renders, everything else is text — and a
 * large binary is refused rather than dumped as mojibake into a <pre>. */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'
import type { TreeEntry } from '../agentd/agent-files'
import Markdown from './Markdown'

const TEXTY = /\.(toml|md|txt|json|ya?ml|py|js|mjs|ts|tsx|css|html|sh|ps1|cfg|ini|log|env)$/i

export function FileViewer({
  entry,
  client,
  onClose,
}: {
  entry: TreeEntry
  client: AgentdClient
  onClose: () => void
}) {
  const [body, setBody] = useState<{ state: 'loading' | 'text' | 'markdown' | 'note'; text: string }>({
    state: 'loading',
    text: 'loading…',
  })
  const url = client.fileUrl(entry.path)
  const media = entry.kind === 'image' || entry.kind === 'video' || entry.kind === 'audio'

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (media) return
    if (!TEXTY.test(entry.name) && entry.size > 512 * 1024) {
      setBody({ state: 'note', text: 'binary file — not shown' })
      return
    }
    let live = true
    setBody({ state: 'loading', text: 'loading…' })
    void (async () => {
      try {
        const text = await (await fetch(url)).text()
        if (!live) return
        setBody({ state: /\.md$/i.test(entry.name) ? 'markdown' : 'text', text })
      } catch (e) {
        if (live) setBody({ state: 'note', text: `could not read: ${String((e as Error)?.message || e)}` })
      }
    })()
    return () => {
      live = false
    }
  }, [entry, url, media])

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal viewer" role="dialog" aria-modal="true" aria-label={entry.name}>
        <header className="modal-head">
          <div className="modal-title">
            <span className="tile sm">▤</span>
            <div>
              <h1>{entry.name}</h1>
              <p>{entry.path}</p>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </header>
        <div className="modal-body viewer-body">
          {entry.kind === 'image' && <img src={url} alt={entry.name} />}
          {entry.kind === 'video' && <video src={url} controls />}
          {entry.kind === 'audio' && <audio src={url} controls />}
          {!media && body.state === 'markdown' && (
            <Markdown text={body.text} />
          )}
          {!media && body.state === 'text' && <pre>{body.text}</pre>}
          {!media && (body.state === 'loading' || body.state === 'note') && (
            <div className="tree-empty">{body.text}</div>
          )}
        </div>
      </div>
    </div>
  )
}
