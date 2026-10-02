/* A chat with no campaign yet: how an ad gets made here, the cast to make it with, and the two
 * recipes. Every button SEEDS the composer — the user still attaches the product photos and sends. */

import type { AgentdClient } from '@agentd/client'
import { UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listCast, type CastMember, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'

const STEPS = [
  ['Brief', 'the look and each shot, as fields you approve'],
  ['Shoot sheet', 'your model in this ad’s outfit and light (wearable-sheet only)'],
  ['Stills', 'checked against the product; you pick, redo or fix'],
  ['Clips', 'animated from the stills you chose'],
]

export function StudioEmpty({ client }: { client: AgentdClient | null }) {
  const seed = useApp((s) => s.seedComposer)
  const tick = useApp((s) => s.studioTick)
  const [cast, setCast] = useState<{ cast: CastMember[]; media: Media } | null>(null)

  useEffect(() => {
    if (!client) return
    listCast(client)
      .then(setCast)
      .catch(() => setCast({ cast: [], media: () => '' }))
  }, [client, tick])

  return (
    <div className="studio-empty">
      <span className="eyebrow-red">How it works</span>
      <h2>Product photos in. A gated ad out.</h2>
      <ol className="how">
        {STEPS.map(([t, d], i) => (
          <li key={t}>
            <span className="how-n">{i + 1}</span>
            <span>
              <b>{t}</b> — {d}
            </span>
          </li>
        ))}
      </ol>

      <span className="eyebrow-red">Your cast</span>
      <div className="cast-pick">
        {(cast?.cast || []).map((m) => (
          <button key={m.name} className="cast-chip" onClick={() => seed(`Make an ad for this product with ${m.name}. `)}>
            <img src={cast!.media(m.sheet)} alt={m.name} />
            <span>{m.name}</span>
          </button>
        ))}
        <button className="cast-chip new" onClick={() => seed('Create a new cast member named : the attached image is AI-generated, not a real person — keep her face exactly. ')}>
          <UserPlus size={18} />
          <span>New model</span>
        </button>
      </div>

      <span className="eyebrow-red">Recipes</span>
      <div className="recipes">
        <button className="recipe" onClick={() => seed('Make an ad for this product, recipe wearable. ')}>
          <b>wearable</b>
          <span>Each clip from its still. Cheapest.</span>
        </button>
        <button className="recipe" onClick={() => seed('Make an ad for this product, recipe wearable-sheet. ')}>
          <b>wearable-sheet</b>
          <span>A shoot sheet first, for a tighter likeness.</span>
        </button>
        <button className="recipe" onClick={() => seed('Make an ad for this product, recipe home-decor. ')}>
          <b>home-decor</b>
          <span>The product alone, styled in a room.</span>
        </button>
      </div>
    </div>
  )
}
