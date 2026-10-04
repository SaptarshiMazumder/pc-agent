/* The cast: the recurring AI models, each with the identity sheet every campaign references.
 * "Use in a new ad" opens a new chat with that member chosen — the studio then offers the recipes.
 * "New cast member" asks the agent for one. Both fill the composer — the user still sends. */

import type { AgentdClient } from '@agentd/client'
import { UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listCast, type CastMember, type CastProposal, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function CastPage({
  client,
  onUse,
  onNew,
}: {
  client: AgentdClient | null
  onUse: (name: string) => void
  onNew: () => void
}) {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ cast: CastMember[]; proposed: CastProposal[]; media: Media } | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!client) return
    listCast(client)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Cast</h1>
        <p className="page-note">Recurring models. One face across every ad is what makes an account followable.</p>
      </header>
      {error && <div className="studio-error">{error}</div>}
      <div className="grid-cards wide">
        {(data?.cast || []).map((m) => (
          <article key={m.name} className="cast-card">
            <button
              className="cast-sheet-btn"
              title="Full view"
              onClick={() => useApp.getState().openViewer({ src: data!.media(m.sheet), kind: 'image', title: `Cast · ${m.name}` })}
            >
              <img src={data!.media(m.sheet)} alt={m.name} loading="lazy" />
            </button>
            <div className="cast-body">
              <span className="camp-name">{m.name}</span>
              <p className="cast-desc">{m.description}</p>
              <button className="prime-btn" onClick={() => onUse(m.name)}>
                Use in a new ad
              </button>
            </div>
          </article>
        ))}
        {(data?.proposed || []).map((p) => (
          <article key={p.name} className="cast-card proposed">
            <div className="cast-sheet-btn">
              {p.references[0] ? <img src={data!.media(p.references[0])} alt={p.name} loading="lazy" /> : <UserPlus size={26} />}
            </div>
            <div className="cast-body">
              <span className="camp-name">{p.name}</span>
              <span className="tag">Proposed — not made yet</span>
              <p className="cast-desc">{p.description}</p>
              <p className="page-note">Pick the model and press Generate in the chat that proposed it.</p>
            </div>
          </article>
        ))}
        <button className="cast-card new" onClick={onNew}>
          <UserPlus size={26} />
          <span>New cast member</span>
        </button>
      </div>
    </div>
  )
}
