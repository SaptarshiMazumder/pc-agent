/* The cast: the recurring AI models, each with the identity sheet every campaign references.
 * "Use" starts a new ad with that member; "New model" asks the agent for one. Both seed the
 * composer — the user still sends. */

import type { AgentdClient } from '@agentd/client'
import { UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listCast, type CastMember, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function CastPage({ client, onUse }: { client: AgentdClient | null; onUse: (prompt: string) => void }) {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ cast: CastMember[]; media: Media } | null>(null)
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
            <a href={data!.media(m.sheet)} target="_blank" rel="noreferrer">
              <img src={data!.media(m.sheet)} alt={m.name} loading="lazy" />
            </a>
            <div className="cast-body">
              <span className="camp-name">{m.name}</span>
              <p className="cast-desc">{m.description}</p>
              <button className="prime-btn" onClick={() => onUse(`Make an ad for this product with ${m.name}. `)}>
                Use in a new ad
              </button>
            </div>
          </article>
        ))}
        <button className="cast-card new" onClick={() => onUse('Create a new cast member named : the attached image is AI-generated, not a real person — keep her face exactly. ')}>
          <UserPlus size={26} />
          <span>New model</span>
        </button>
      </div>
    </div>
  )
}
