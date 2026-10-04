/* Every campaign, newest first: what it is, the step it is on, what it made and what it cost.
 * Opening one shows it in the studio of the chat that started it (or pins it into this chat when
 * it came from elsewhere). */

import type { AgentdClient } from '@agentd/client'
import { Film, Image as ImageIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listCampaigns, money, type CampaignRow, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function CampaignsPage({
  client,
  onOpen,
}: {
  client: AgentdClient | null
  onOpen: (campaign: string) => void
}) {
  const session = useApp((s) => s.currentSessionKey)
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ rows: CampaignRow[]; media: Media } | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!client) return
    listCampaigns(client, session)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, session, tick])

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Campaigns</h1>
        <p className="page-note">{data ? `${data.rows.length} campaign${data.rows.length === 1 ? '' : 's'}` : 'Loading…'}</p>
      </header>
      {error && <div className="studio-error">{error}</div>}
      <div className="grid-cards">
        {(data?.rows || []).map((r) => (
          <button key={r.id} className="camp-card" onClick={() => onOpen(r.id)}>
            <div className="camp-cover">
              {r.cover ? <img src={data!.media(r.cover)} alt={r.name} loading="lazy" /> : null}
              <span className={`gate-badge small${r.step === 'done' ? ' g-done' : ''}`}>{r.step}</span>
            </div>
            <div className="camp-body">
              <span className="camp-name">{r.name}</span>
              <span className="camp-sub">
                {r.id}
                {r.cast ? ` · ${r.cast}` : ''}
              </span>
              <span className="camp-meta">
                <span>
                  <ImageIcon size={12} /> {r.stills}
                </span>
                <span>
                  <Film size={12} /> {r.clips}
                </span>
                <span className="push-end">{money(r.spent_usd)}</span>
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
