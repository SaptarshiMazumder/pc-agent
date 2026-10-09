/* The top of a campaign: what it is (recipe, product, who is in it), how runs are approved, and
 * what it has spent of its budget. The cast and product chips open the pictures every run
 * references (CampaignAssets) — the cast sheet and the product photos. */

import type { AgentdClient } from '@agentd/client'
import { useState } from 'react'

import { money, setApproval, type CampaignDetail, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { CampaignAssets } from './CampaignAssets'

export function CampaignHeader({ client, campaign, media }: { client: AgentdClient | null; campaign: CampaignDetail; media: Media }) {
  const bump = useApp((s) => s.bumpStudio)
  const [assets, setAssets] = useState(false)
  const spent = campaign.budget_usd > 0 ? Math.min(100, (campaign.spent_usd / campaign.budget_usd) * 100) : 0

  return (
    <>
      <header className="camp-head">
        <div className="camp-head-title">
          <span className="eyebrow-red" title={campaign.recipe_title}>
            {campaign.recipe_name}
          </span>
          <h2>{campaign.product.name}</h2>
          <div className="camp-chips">
            {campaign.cast && (
              <button className={`camp-chip${assets ? ' on' : ''}`} onClick={() => setAssets(!assets)} title="The cast sheet every shot references">
                <img src={media(campaign.cast.sheet)} alt="" className="camp-chip-face" />
                {campaign.cast.name}
              </button>
            )}
            <button className={`camp-chip${assets ? ' on' : ''}`} onClick={() => setAssets(!assets)} title="The product photos every shot references">
              <span className="camp-chip-stack">
                {campaign.product.photos.slice(0, 3).map((p) => (
                  <img key={p} src={media(p)} alt="" />
                ))}
              </span>
              {campaign.product.photos.length} product photo{campaign.product.photos.length === 1 ? '' : 's'}
            </button>
            <span className="camp-id">{campaign.campaign_id}</span>
          </div>
        </div>

        <div className="camp-controls">
          <div className="camp-control">
            <span className="strip-label">Before generating</span>
            <div className="seg-switch" role="radiogroup" aria-label="Model approval">
              {(['ask', 'auto'] as const).map((mode) => (
                <button
                  key={mode}
                  role="radio"
                  aria-checked={campaign.approval === mode}
                  className={campaign.approval === mode ? 'on' : ''}
                  disabled={!client}
                  onClick={() => client && void setApproval(client, campaign.campaign_id, mode).then(bump)}
                  title={
                    mode === 'ask'
                      ? 'Nothing is generated until you press Generate here — that click approves its model'
                      : 'The agent generates when you ask it in the chat'
                  }
                >
                  {mode === 'ask' ? 'Ask me' : 'Auto'}
                </button>
              ))}
            </div>
          </div>
          <div className="camp-control camp-budget" title="Spent so far / the campaign's budget">
            <span className="strip-label">Budget</span>
            <span className="camp-budget-line">
              <span className="money-now">{money(campaign.spent_usd)}</span>
              <span className="money-of">of {money(campaign.budget_usd)}</span>
            </span>
            <span className="camp-budget-bar">
              <span style={{ width: `${spent}%` }} />
            </span>
          </div>
        </div>
      </header>
      {assets && <CampaignAssets campaign={campaign} media={media} onClose={() => setAssets(false)} />}
    </>
  )
}
