/* A new ad's story: the occasions coming up and the story themes that suit them and the chosen
 * products (best first), or the user's own story. A click adds its sentence to the composer. */

import type { AgentdClient } from '@agentd/client'
import { Clapperboard, Image as ImageIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { suggestStories, type Occasion, type StoryTheme } from '../agentd/stories'

export function StoryPicker({
  client,
  products,
  chosen,
  onPick,
}: {
  client: AgentdClient | null
  products: string[]
  chosen: string
  onPick: (key: string) => void
}) {
  const [data, setData] = useState<{ occasions: Occasion[]; themes: StoryTheme[] } | null>(null)
  const [error, setError] = useState('')
  const [all, setAll] = useState(false)
  const key = products.join('|')
  useEffect(() => {
    if (!client) return
    suggestStories(client, products)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, key])

  const themes = (data?.themes || []).slice(0, all ? undefined : 6)
  return (
    <div className="story-picker">
      {error && <div className="studio-error">Could not load the stories: {error}</div>}
      {data && data.occasions.length > 0 && (
        <div className="occasion-row">
          {data.occasions.slice(0, 6).map((o) => (
            <span key={o.key} className={`occasion-chip${o.posting_now ? ' now' : ''}`}>
              <b>{o.name}</b>
              <span>{new Date(o.next).toLocaleDateString([], { month: 'short', day: 'numeric' })}</span>
              {o.posting_now && <em>post now</em>}
            </span>
          ))}
        </div>
      )}
      <div className="theme-grid compact">
        <button className={`theme-card theme-own${chosen === 'own' ? ' on' : ''}`} onClick={() => onPick('own')}>
          <b>Tell your own story</b>
          <span>Say what the ad should say, in your words.</span>
        </button>
        {themes.map((t) => (
          <button key={t.key} className={`theme-card${chosen === t.key ? ' on' : ''}`} onClick={() => onPick(t.key)} title={t.summary}>
            <header>
              <b>{t.name}</b>
              <span className="recipe-out">{t.format}</span>
            </header>
            {t.hooks[0] && <q>{t.hooks[0]}</q>}
            <span className="theme-shots-line">
              {t.shots.map((s) => (
                <span key={s.id}>{s.kind === 'video' ? <Clapperboard size={10} /> : <ImageIcon size={10} />}</span>
              ))}{' '}
              {t.shots.length} shots
            </span>
            <span className="recipe-meta">{t.why_now}</span>
          </button>
        ))}
      </div>
      {(data?.themes.length || 0) > 6 && (
        <button className="link-red" onClick={() => setAll(!all)}>
          {all ? 'Fewer stories' : `All ${data!.themes.length} stories`}
        </button>
      )}
    </div>
  )
}
