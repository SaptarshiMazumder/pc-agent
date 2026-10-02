/* The gate the campaign waits at: what the step made, and the user's decisions about it, sent as
 * ONE answer.
 *
 * THE ANSWER IS A CHAT MESSAGE. "Send answer" composes the decisions into plain words and sends
 * them the way the user would type them; the agent maps them onto campaign_run's picks and redos
 * (and still_fix for a fix). The daemon stamps that message as the answer to the gate's ask, so a
 * gate opens only for a reply that arrived after its results — the same rule as typing it.
 */

import type { AgentdClient } from '@agentd/client'
import { Send } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  composeAnswer,
  GATE_LABEL,
  listVideoModels,
  type CampaignDetail,
  type Decision,
  type Media,
  type VideoModel,
} from '../agentd/campaigns'
import { BriefView } from './BriefView'
import { ClipsView } from './ClipsView'
import { DecisionBar } from './DecisionBar'
import { DoneView } from './DoneView'
import { StillsBoard } from './StillsBoard'

export function GatePanel({
  client,
  campaign,
  media,
  busy,
  onAnswer,
}: {
  client: AgentdClient | null
  campaign: CampaignDetail
  media: Media
  /** A run is going — answering now would land in the middle of it. */
  busy: boolean
  onAnswer: (text: string) => void
}) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})

  /* THE CLIP MODEL, chosen before the clips are made (stills gate) or for a redo (clips gate).
     The list is the agent's video specs — a model added there shows up here. */
  const [models, setModels] = useState<{ models: VideoModel[]; fallback: string } | null>(null)
  const [video, setVideo] = useState('')
  useEffect(() => {
    if (!client) return
    listVideoModels(client)
      .then(setModels)
      .catch(() => setModels(null))
  }, [client])
  const current = campaign.video || models?.fallback || ''
  const picksModel = (campaign.gate === 'stills' || campaign.gate === 'clips') && campaign.plan.animate.length > 0

  // A new gate (or new results at the same gate, after a redo) starts with nothing decided.
  const stamp = `${campaign.campaign_id}:${campaign.gate}:${campaign.updated}`
  useEffect(() => {
    setDecisions({})
    setVideo('')
  }, [stamp])

  const decide = (about: string, d: Decision) => setDecisions((all) => ({ ...all, [about]: d }))

  const abouts = useMemo(() => {
    if (campaign.gate === 'brief') return ['brief']
    if (campaign.gate === 'sheet') return ['sheet']
    if (campaign.gate === 'stills') return campaign.shots.map((s) => s.shot_id)
    if (campaign.gate === 'clips') return campaign.shots.filter((s) => campaign.plan.animate.includes(s.shot_id)).map((s) => s.shot_id)
    return []
  }, [campaign])

  const full = Object.fromEntries(abouts.map((a) => [a, decisions[a] || ({ kind: 'keep' } as Decision)]))
  const incomplete = Object.values(full).some((d) => (d.kind === 'redo' || d.kind === 'fix') && !d.change.trim())
  const extra = picksModel && video && video !== current ? [`- clip model: ${video}`] : []
  const answer = composeAnswer(campaign.campaign_id, campaign.gate, full, extra)

  if (campaign.gate === 'done') return <DoneView campaign={campaign} media={media} />

  return (
    <div className="gate">
      <div className="gate-body">
        {campaign.gate === 'brief' && (
          <>
            <BriefView campaign={campaign} />
            <DecisionBar value={full.brief} onChange={(d) => decide('brief', d)} disabled={busy} />
          </>
        )}
        {campaign.gate === 'sheet' && (
          <div className="sheet">
            {campaign.sheet ? (
              <a href={media(campaign.sheet)} target="_blank" rel="noreferrer">
                <img className="sheet-img" src={media(campaign.sheet)} alt="shoot sheet" />
              </a>
            ) : (
              <div className="shot-empty">The sheet failed its identity check — redo it with a change.</div>
            )}
            {campaign.sheet && <span className="score inline">{campaign.sheet_score}/10</span>}
            <DecisionBar value={full.sheet} onChange={(d) => decide('sheet', d)} disabled={busy} />
          </div>
        )}
        {campaign.gate === 'stills' && (
          <StillsBoard campaign={campaign} media={media} decisions={full} onDecide={decide} disabled={busy} />
        )}
        {campaign.gate === 'clips' && (
          <ClipsView campaign={campaign} media={media} decisions={full} onDecide={decide} disabled={busy} />
        )}
      </div>

      {picksModel && models && (
        <label className="model-pick">
          <span className="strip-label">Clip model</span>
          <select value={video || current} disabled={busy} onChange={(e) => setVideo(e.target.value)}>
            {models.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
                {m.price ? ` — ${m.price}` : ''}
                {m.references ? ' · uses reference images' : ''}
              </option>
            ))}
          </select>
        </label>
      )}

      <footer className="gate-foot">
        <pre className="gate-preview">{answer}</pre>
        <button
          className="prime-btn gate-send"
          disabled={busy || incomplete}
          onClick={() => onAnswer(answer)}
          title={busy ? 'Wait for the current run to finish' : `Send your ${GATE_LABEL[campaign.gate].toLowerCase()} answer`}
        >
          <Send size={14} /> Send answer
        </button>
      </footer>
    </div>
  )
}
