/* One file, opened from the tree. Media plays, markdown renders, everything else is text — and a
 * large binary is refused rather than dumped as mojibake into a <pre>. */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'
import type { TreeEntry } from '../agentd/agent-files'
import { renderMarkdown } from '../markdown/md'

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
    <div className="viewer-back" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="viewer glass">
        <header>
          <div className="viewer-title">
            <span className="vname">{entry.name}</span>
            <span className="vpath">{entry.path}</span>
          </div>
          <button className="icon-btn" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="viewer-body">
          {entry.kind === 'image' && <img src={url} alt={entry.name} />}
          {entry.kind === 'video' && <video src={url} controls />}
          {entry.kind === 'audio' && <audio src={url} controls />}
          {!media && body.state === 'markdown' && (
            <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(body.text) }} />
          )}
          {!media && body.state === 'text' && <pre>{body.text}</pre>}
          {!media && (body.state === 'loading' || body.state === 'note') && body.text}
        </div>
      </div>
    </div>
  )
}
