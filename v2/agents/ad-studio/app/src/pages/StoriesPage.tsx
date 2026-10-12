/* Stories — what every ad starts from: the occasions coming up (posting now first), the story themes
 * that suit them (each a default story with its shot list — loose, the agent adapts it to your
 * products and words), and your own stories. "Use this story" opens a new chat with it chosen.
 * The templates (how shots get made) are one link away, for when you want to pick the stages. */

import type { AgentdClient } from '@agentd/client'
import { ArrowRight, CalendarDays, Clapperboard, Image as ImageIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listStories, suggestStories, type Occasion, type Story, type StoryTheme } from '../agentd/stories'
import { useApp } from '../state/store'

type Kind = 'all' | 'wear' | 'jewellery'

export function StoriesPage({
  client,
  onUse,
  onOwn,
  onOpen,
  onTemplates,
}: {
  client: AgentdClient | null
  onUse: (theme: StoryTheme) => void
  onOwn: () => void
  onOpen: (story: Story) => void
  onTemplates: () => void
}) {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ today: string; occasions: Occasion[]; themes: StoryTheme[] } | null>(null)
  const [mine, setMine] = useState<Story[]>([])
  const [kind, setKind] = useState<Kind>('all')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!client) return
    Promise.all([suggestStories(client), listStories(client)])
      .then(([s, m]) => {
        setData(s)
        setMine(m)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  const themes = (data?.themes || []).filter((t) => kind === 'all' || t.kinds.includes(kind))

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Stories</h1>
        <p className="page-note">
          Every ad tells a story — what it says, who it's for, and exactly what to make. Start from one of these, or tell your own; you can change
          it any time.
        </p>
      </header>
      {error && <div className="studio-error">Could not load the stories: {error}</div>}

      {data && data.occasions.length > 0 && (
        <section className="occasion-strip">
          <span className="strip-label">
            <CalendarDays size={12} /> Coming up
          </span>
          <div className="occasion-row">
            {data.occasions.map((o) => (
              <span key={o.key} className={`occasion-chip${o.posting_now ? ' now' : ''}`} title={o.wear || o.jewellery}>
                <b>{o.name}</b>
                <span>{new Date(o.next).toLocaleDateString([], { month: 'short', day: 'numeric' })}</span>
                {o.posting_now && <em>post now</em>}
              </span>
            ))}
          </div>
        </section>
      )}

      {mine.length > 0 && (
        <section className="new-ad-section">
          <h3 className="new-ad-h">Your stories</h3>
          <div className="my-stories">
            {mine.map((s) => (
              <button key={s.id} className="my-story" onClick={() => onOpen(s)} title="Open its chat">
                <b>{s.title}</b>
                <span>
                  v{s.version} · {s.status} · {s.shots.length} shots
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="new-ad-section">
        <div className="stories-head">
          <h3 className="new-ad-h">Start from a story</h3>
          <div className="seg-switch small" role="radiogroup" aria-label="Which products">
            {(['all', 'wear', 'jewellery'] as const).map((k) => (
              <button key={k} role="radio" aria-checked={kind === k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
                {k === 'all' ? 'All' : k === 'wear' ? 'Ethnic wear' : 'Jewellery'}
              </button>
            ))}
          </div>
        </div>
        <div className="theme-grid">
          <button className="theme-card theme-own" disabled={!client} onClick={onOwn}>
            <b>Tell your own story</b>
            <span>Say what the ad should say — the agent writes the shot list with you.</span>
          </button>
          {themes.map((t) => (
            <article key={t.key} className="theme-card">
              <header>
                <b>{t.name}</b>
                <span className="recipe-out">{t.format}</span>
              </header>
              <p>{t.summary}</p>
              {t.hooks[0] && <q>{t.hooks[0]}</q>}
              <ol className="theme-shots">
                {t.shots.map((s) => (
                  <li key={s.id}>
                    {s.kind === 'video' ? <Clapperboard size={11} /> : <ImageIcon size={11} />} {s.purpose}
                  </li>
                ))}
              </ol>
              <footer>
                <span className="recipe-meta">{t.why_now}</span>
                <button className="prime-btn" disabled={!client} onClick={() => onUse(t)}>
                  Use this story <ArrowRight size={13} />
                </button>
              </footer>
            </article>
          ))}
        </div>
      </section>

      <p className="page-note">
        Want to choose the stages yourself?{' '}
        <button className="link-red" onClick={onTemplates}>
          See the templates
        </button>
      </p>
    </div>
  )
}
