/* A post chat before its post exists: the collections, as cards. A click puts "Make an Instagram
 * post from the collection <name>." in the composer — the user adds anything (the hook, the order,
 * where each product was found) and sends. */

import type { AgentdClient } from '@agentd/client'
import { Check, FolderOpen } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listCollections, POST_START, type Collection } from '../agentd/posts'
import type { Media } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function PostOpening({ client, session }: { client: AgentdClient | null; session: string }) {
  const tick = useApp((s) => s.studioTick)
  const append = useApp((s) => s.appendComposer)
  const setStart = useApp((s) => s.setStart)
  const chosen = useApp((s) => s.starts[session]?.collection || '')
  const [data, setData] = useState<{ collections: Collection[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!client) return
    listCollections(client)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  return (
    <div className="opening">
      <span className="opening-eyebrow">Posts</span>
      <h2 className="opening-headline">Which collection is this post from?</h2>
      <p className="opening-blurb">Pick one, add anything you want — the hook, the order, where each product was found — and send.</p>
      {error && <div className="studio-error">Could not load the collections: {error}</div>}
      {data && !data.collections.length && (
        <div className="ref-none">No collections yet — select images and clips in a campaign's Generations tab and add them to one.</div>
      )}
      <div className="collection-cards">
        {(data?.collections || []).map((c) => (
          <button
            key={c.slug}
            className={`collection-card${chosen === c.slug ? ' on' : ''}`}
            disabled={!c.items.length}
            onClick={() => {
              append(POST_START(c.name))
              setStart(session, { collection: c.slug })
            }}
          >
            <span className="collection-thumbs">
              {c.items.slice(0, 4).map((i) =>
                i.kind === 'video' ? <video key={i.path} src={data!.media(i.path)} muted preload="metadata" /> : <img key={i.path} src={data!.media(i.path)} alt="" />,
              )}
            </span>
            <span className="collection-name">
              {chosen === c.slug ? <Check size={12} /> : <FolderOpen size={12} />} {c.name}
            </span>
            <span className="collection-meta">
              {c.items.length} items · {[...new Set(c.items.map((i) => i.product))].join(', ')}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
