/* The studio — the right-hand side of a chat: the campaign this chat is on, the gate it waits at,
 * and what the cast member and the product look like. Presentation only: it reads through the
 * agent's read-only tools and acts by handing the user's answer to the chat. */

import type { AgentdClient } from '@agentd/client'
import { Loader2 } from 'lucide-react'

import { GATE_LABEL, money } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { GatePanel } from './GatePanel'
import { GateStepper } from './GateStepper'
import { StudioEmpty } from './StudioEmpty'
import { useCampaign } from './useCampaign'

const NO_JOBS: Record<string, { tool: string; text: string }> = {}

export function Studio({
  client,
  session,
  running,
  onAnswer,
}: {
  client: AgentdClient | null
  session: string
  running: boolean
  onAnswer: (text: string) => void
}) {
  const pinned = useApp((s) => s.pinned[session] || '')
  // The selector returns the stored object itself (or undefined) — never a fresh `{}`, which
  // zustand would see as a change on every render and loop on.
  const jobs = useApp((s) => s.sessions[session]?.jobs) ?? NO_JOBS
  const { campaign, media, error, loading } = useCampaign(client, session, pinned)
  const working = Object.values(jobs)

  return (
    <section className="studio">
      {working.length > 0 && (
        <div className="job-strip">
          {working.map((j, i) => (
            <span key={i} className="job">
              <Loader2 size={13} className="spin" /> {j.text || `${j.tool} working…`}
            </span>
          ))}
        </div>
      )}

      {error && <div className="studio-error">Could not read the campaign: {error}</div>}

      {!campaign ? (
        !loading && <StudioEmpty client={client} />
      ) : (
        <>
          <header className="studio-head">
            <div className="studio-title">
              <span className="eyebrow-red">{campaign.recipe_title || campaign.recipe_key}</span>
              <h2>{campaign.product.name}</h2>
              <span className="studio-sub">
                {campaign.campaign_id}
                {campaign.cast ? ` · ${campaign.cast.name}` : ''}
              </span>
            </div>
            <div className="studio-money" title="Spent so far / the run's budget">
              <span className="money-now">{money(campaign.spent_usd)}</span>
              <span className="money-of">of {money(campaign.plan.budget_usd)}</span>
            </div>
          </header>

          <GateStepper gate={campaign.gate} gates={campaign.plan.gates} hasSheet={!!campaign.sheet} />

          <div className="gate-title">
            <span className="gate-badge">{GATE_LABEL[campaign.gate]}</span>
            {campaign.gate !== 'done' && <span className="gate-hint">Decide, then send your answer — nothing moves until you do.</span>}
          </div>

          <GatePanel client={client} campaign={campaign} media={media} busy={running || working.length > 0} onAnswer={onAnswer} />

          <div className="studio-strip">
            {campaign.cast && (
              <div className="strip-card">
                <span className="strip-label">Cast · {campaign.cast.name}</span>
                <a href={media(campaign.cast.sheet)} target="_blank" rel="noreferrer">
                  <img src={media(campaign.cast.sheet)} alt={campaign.cast.name} />
                </a>
              </div>
            )}
            <div className="strip-card">
              <span className="strip-label">Product · {campaign.product.category}</span>
              <div className="strip-photos">
                {campaign.product.photos.map((p) => (
                  <img key={p} src={media(p)} alt="product" />
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
