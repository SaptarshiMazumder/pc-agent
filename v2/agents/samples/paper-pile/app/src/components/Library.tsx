import { useEffect, useMemo, useState } from 'react'

export interface Doc {
  file: string
  path: string
  title: string
  source: string
  added: string
  tags: string[]
  summary: string
}

export interface IndexedFile {
  slug: string
  title: string
  source_path: string
  source_name: string
  filed_copy: string
  size: number
  indexed_at: string
  chunks: number
  embedded_chunks: number
  search_mode: 'semantic + lexical' | 'lexical'
}

/** The library, plus search.
 *
 *  TWO KINDS OF SEARCH, deliberately. Typing filters the TITLES already on screen — instant,
 *  local, no round trip. Pressing Enter runs `library_search`, which reads the full text of every
 *  note on disk. The first answers "where is that paper", the second answers "what did I read
 *  about X", and conflating them makes one of the two feel broken. */
export function Library({
  docs,
  indexedFiles,
  invoke,
  onOpen,
}: {
  docs: Doc[]
  indexedFiles: IndexedFile[]
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
  onOpen: (doc: Doc) => void
}) {
  const [query, setQuery] = useState('')
  const [tag, setTag] = useState('')
  const [hits, setHits] = useState<null | Array<{ file: string; title: string; text: string }>>(null)
  const [searching, setSearching] = useState(false)

  const tags = useMemo(() => {
    const seen = new Map<string, number>()
    for (const d of docs) for (const t of d.tags) seen.set(t, (seen.get(t) ?? 0) + 1)
    return [...seen.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)
  }, [docs])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return docs.filter(
      (d) =>
        (!tag || d.tags.includes(tag)) &&
        (!q || d.title.toLowerCase().includes(q) || d.summary.toLowerCase().includes(q)),
    )
  }, [docs, query, tag])

  useEffect(() => {
    // A stale full-text result under a changed query is worse than none.
    setHits(null)
  }, [query])

  const runFullText = async () => {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    try {
      const raw = await invoke('library_search', { query: q })
      setHits(raw.trim().startsWith('{') ? JSON.parse(raw).matches ?? [] : [])
    } catch {
      setHits([])
    } finally {
      setSearching(false)
    }
  }

  if (!docs.length && !indexedFiles.length) {
    return (
      <div className="empty-state">
        <h1>The library is empty</h1>
        <p>Drop a PDF or a paper on the left. I read it, index its full text, and file it here.</p>
      </div>
    )
  }

  const formatBytes = (bytes: number) => {
    if (!bytes) return 'size unavailable'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="scroll">
      <header className="library-head">
        <div>
          <h1>Indexed files</h1>
          <p>Every source whose full text is in retrieval — not generated summary cards.</p>
        </div>
        <span className="inventory-total">{indexedFiles.length} indexed</span>
      </header>

      <ul className="indexed-files">
        {indexedFiles.map((f) => (
          <li key={f.slug}>
            <div className="indexed-file">
              <span className="file-icon" aria-hidden="true">▧</span>
              <span className="file-details">
                <strong>{f.source_name}</strong>
                <span className="file-title">{f.title}</span>
                <span className="file-path">{f.filed_copy || f.source_path || 'Indexed from supplied text'}</span>
              </span>
              <span className="file-stats">
                <span className={`rag-status ${f.search_mode === 'semantic + lexical' ? 'semantic' : ''}`}>
                  {f.search_mode}
                </span>
                <span>{f.chunks} chunks · {f.embedded_chunks}/{f.chunks} embedded</span>
                <span>{formatBytes(f.size)}{f.indexed_at ? ` · indexed ${f.indexed_at}` : ''}</span>
              </span>
            </div>
          </li>
        ))}
        {indexedFiles.length === 0 && (
          <li className="inventory-empty">No source files are in the RAG index yet.</li>
        )}
      </ul>

      <div className="notes-heading">
        <h2>Reading notes</h2>
        <p>{docs.length} generated {docs.length === 1 ? 'note' : 'notes'} for browsing and connections.</p>
      </div>
      <div className="search">
        <input
          value={query}
          placeholder="Filter by title — press Enter to search inside every note"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void runFullText()}
        />
        {query && (
          <button className="ghost" onClick={() => void runFullText()} disabled={searching}>
            {searching ? 'searching…' : 'Search full text'}
          </button>
        )}
      </div>

      {tags.length > 0 && (
        <div className="tags">
          <button className={!tag ? 'on' : ''} onClick={() => setTag('')}>
            all
          </button>
          {tags.map(([t, n]) => (
            <button key={t} className={tag === t ? 'on' : ''} onClick={() => setTag(t)}>
              {t} <span className="n">{n}</span>
            </button>
          ))}
        </div>
      )}

      {hits && (
        <section className="hits">
          <h2>{hits.length} match{hits.length === 1 ? '' : 'es'} inside notes</h2>
          {hits.length === 0 && <p className="muted">Nothing in the notes contains that.</p>}
          {hits.map((h, i) => (
            <button key={i} className="hit" onClick={() => {
              const doc = docs.find((d) => d.file === h.file)
              if (doc) onOpen(doc)
            }}>
              <span className="hit-title">{h.title}</span>
              <span className="hit-text">{h.text}</span>
            </button>
          ))}
        </section>
      )}

      <ul className="docs">
        {shown.map((d) => (
          <li key={d.file}>
            <button className="doc" onClick={() => onOpen(d)}>
              <span className="doc-title">{d.title}</span>
              <span className="doc-sum">{d.summary}</span>
              <span className="doc-meta">
                {d.added && <span>{d.added}</span>}
                {d.tags.map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </span>
            </button>
          </li>
        ))}
        {shown.length === 0 && <li className="empty">No document matches that filter.</li>}
      </ul>
    </div>
  )
}
