/* Everything a campaign made: every sheet, still and clip, newest first, with the model that made
 * it, what it cost, how its check went, and whether it is the one in use. Built from the campaign's
 * own records (its cost ledger and its checks) by campaign_generations.
 *
 * Any of them can be SELECTED (click it): the selection shows above the composer, and the next
 * message is about exactly those files — fix this one, more like it, a video from it, extend that
 * clip. The expand button opens one full screen. The "Show" chips only filter the list. */

import type { AgentdClient } from '@agentd/client'
import { CheckCircle2, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { listGenerations, money, type Generation, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { MediaTileActions } from './MediaTileActions'

type Filter = 'all' | 'images' | 'clips' | 'picked' | 'flagged'
const FILTERS: Filter[] = ['all', 'images', 'clips', 'picked', 'flagged']

function when(ts: number): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function GenerationsTab({ client, campaign }: { client: AgentdClient | null; campaign: string }) {
  const tick = useApp((s) => s.studioTick)
  const selection = useApp((s) => s.selection)
  const toggle = useApp((s) => s.toggleSelected)
  const [data, setData] = useState<{ rows: Generation[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  useEffect(() => {
    if (!client) return
    listGenerations(client, campaign)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, campaign, tick])

  const rows = useMemo(() => {
    const all = data?.rows || []
    if (filter === 'all') return all
    if (filter === 'flagged') return all.filter((r) => r.passed === false)
    if (filter === 'picked') return all.filter((r) => r.in_use)
    return all.filter((r) => (filter === 'clips' ? r.kind === 'video' : r.kind !== 'video'))
  }, [data, filter])

  const total = (data?.rows || []).reduce((n, r) => n + r.cost_usd, 0)

  return (
    <div className="gens">
      <div className="gens-bar">
        <div className="gens-filters" role="radiogroup" aria-label="Show">
          <span className="filter-label">Show</span>
          {FILTERS.map((f) => (
            <button
              key={f}
              role="radio"
              aria-checked={filter === f}
              className={`filter-chip${filter === f ? ' on' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
        <span className="gens-total">
          {(data?.rows || []).length} made · {money(total)}
        </span>
      </div>
      {error && <div className="studio-error">{error}</div>}
      <div className="gens-grid">
        {rows.map((r) => {
          const kind = r.kind === 'video' ? ('video' as const) : ('image' as const)
          const item = { path: r.path, kind, campaign, shot: r.step || '', src: data!.media(r.path) }
          const selected = selection.some((x) => x.path === r.path)
          return (
          <figure
            key={r.path}
            className={`gen${r.in_use ? ' in-use' : ''}${r.passed === false ? ' failed' : ''}${selected ? ' selected' : ''}`}
          >
            <div
              role="button"
              tabIndex={0}
              className="gen-media"
              title={selected ? 'Selected — click to unselect' : 'Click to select it for your next message'}
              onClick={() => toggle(item)}
            >
              {r.kind === 'video' ? (
                <video src={data!.media(r.path)} muted loop playsInline preload="metadata" onMouseEnter={(e) => void e.currentTarget.play()} onMouseLeave={(e) => e.currentTarget.pause()} />
              ) : (
                <img src={data!.media(r.path)} alt={r.path} loading="lazy" />
              )}
              {r.in_use && <span className="chosen-tag">Picked</span>}
              <MediaTileActions item={item} title={`${r.step_title} · ${r.path.split('/').pop()}`} />
            </div>
            <figcaption>
              <div className="gen-line">
                <span className="gen-stage">
                  {r.step_title}
                </span>
                {r.score !== null && (
                  <span className={`gen-score ${r.passed ? 'ok' : 'bad'}`}>
                    {r.passed ? <CheckCircle2 size={12} /> : <XCircle size={12} />} {r.score}/10
                  </span>
                )}
                {r.score === null && r.kind === 'video' && <span className="gen-score">not checked</span>}
              </div>
              <div className="gen-model">
                {r.provider}/{r.model}
              </div>
              <div className="gen-meta">
                <span>{r.credits ? `${r.credits} cr · ` : ''}{money(r.cost_usd)}</span>
                <span>{when(r.made_at)}</span>
              </div>
              {r.problems.length > 0 && (
                <ul className="gen-problems">
                  {r.problems.map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              )}
            </figcaption>
          </figure>
          )
        })}
        {data && rows.length === 0 && <div className="gens-empty">Nothing here yet.</div>}
      </div>
    </div>
  )
}
