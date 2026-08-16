import { useEffect, useState } from 'react'
import type { Doc } from './Library'

/** One note, read from disk with the agent's own `read` tool.
 *
 *  Rendered as plain text in a <pre>, NOT as parsed markdown. The note is the artifact; showing
 *  it byte-for-byte means what you read is what is stored and what the search matched. A
 *  renderer would also have to be trusted with untrusted content, for a document nobody is
 *  publishing. */
export function Reader({
  doc,
  invoke,
  onBack,
}: {
  doc: Doc
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
  onBack: () => void
}) {
  const [text, setText] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    setText('')
    setError('')
    invoke('read', { path: doc.path })
      .then((t) => live && setText(t))
      // The note exists in the index but not on disk: it was deleted or renamed since the last
      // load. Say which file, so the user can see what happened.
      .catch((e) => live && setError(`could not read ${doc.file}: ${(e as Error)?.message ?? e}`))
    return () => {
      live = false
    }
  }, [doc, invoke])

  return (
    <div className="scroll reader">
      <button className="back" onClick={onBack}>
        ← Library
      </button>
      <h1>{doc.title}</h1>
      <div className="doc-meta">
        {doc.added && <span>{doc.added}</span>}
        {doc.tags.map((t) => (
          <span key={t} className="tag">
            {t}
          </span>
        ))}
      </div>
      {doc.source && (
        <p className="source">
          Source: <span>{doc.source}</span>
        </p>
      )}
      {error && <p className="err">{error}</p>}
      {!text && !error && <p className="muted">reading…</p>}
      {text && <pre className="note">{text}</pre>}
    </div>
  )
}
