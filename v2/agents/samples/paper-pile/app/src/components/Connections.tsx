import { useEffect, useState } from 'react'
import type { Doc } from './Library'

interface Link {
  from: string
  to: string
  resolves: boolean
}

interface Graph {
  links: Link[]
  orphans: string[]
  broken: string[]
}

/** What the list view cannot show: the shape of the library.
 *
 *  A list of thirty notes looks the same whether they are deeply cross-referenced or thirty
 *  strangers in a folder. `library_links` is the only view that tells them apart — and it is a
 *  direct tool call, so this whole page costs nothing and is always current.
 *
 *  ORPHANS AND BROKEN LINKS ARE THE POINT. They are the two failures that accumulate silently:
 *  a note filed and never connected, and a `[[link]]` to a note that was never written. Both are
 *  invisible in a list and obvious here. */
export function Connections({
  docs,
  invoke,
  onOpen,
}: {
  docs: Doc[]
  invoke: (name: string, params?: Record<string, unknown>) => Promise<string>
  onOpen: (doc: Doc) => void
}) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    invoke('library_links', {})
      .then((raw) => {
        if (!live) return
        setGraph(JSON.parse(raw))
        setError('')
      })
      // Surfaced, not swallowed: an empty graph and a failed call look identical otherwise, and
      // "you have no connections" is a lie the user would act on.
      .catch((e) => live && setError(`could not read the link graph: ${(e as Error)?.message ?? e}`))
    return () => {
      live = false
    }
  }, [invoke])

  const open = (slug: string) => {
    const doc = docs.find((d) => d.file === `${slug}.md`)
    if (doc) onOpen(doc)
  }

  if (error) return <p className="err">{error}</p>
  if (!graph) return <p className="muted pad">Reading the link graph…</p>

  const real = graph.links.filter((l) => l.resolves)

  return (
    <div className="scroll">
      <div className="graph-stats">
        <span>
          <b>{real.length}</b> connection{real.length === 1 ? '' : 's'}
        </span>
        <span>
          <b>{graph.orphans.length}</b> unconnected
        </span>
        <span className={graph.broken.length ? 'bad' : ''}>
          <b>{graph.broken.length}</b> broken
        </span>
      </div>

      {graph.broken.length > 0 && (
        <section className="panel bad">
          <h2>Links pointing at notes that do not exist</h2>
          <p className="muted">
            Something in the library references these. Either the note was never written, or the
            slug is a typo.
          </p>
          <ul className="chips">
            {graph.broken.map((slug) => (
              <li key={slug} className="chip bad">
                {slug}
              </li>
            ))}
          </ul>
        </section>
      )}

      {graph.orphans.length > 0 && (
        <section className="panel">
          <h2>Filed, but connected to nothing</h2>
          <p className="muted">
            Nothing links to these and they link to nothing. Ask whether they relate to anything
            you already have.
          </p>
          <ul className="chips">
            {graph.orphans.map((slug) => (
              <li key={slug}>
                <button className="chip" onClick={() => open(slug)}>
                  {slug}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>Connections</h2>
        {real.length === 0 ? (
          <p className="muted">
            No note links to another yet. Links appear as <code>[[slug]]</code> in a note's
            “Connects to” section.
          </p>
        ) : (
          <ul className="edges">
            {real.map((l, i) => (
              <li key={i}>
                <button className="chip" onClick={() => open(l.from)}>
                  {l.from}
                </button>
                <span className="arrow">→</span>
                <button className="chip" onClick={() => open(l.to)}>
                  {l.to}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
