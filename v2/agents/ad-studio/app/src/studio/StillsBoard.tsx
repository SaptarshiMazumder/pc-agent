/* The stills gate: per shot, every still that passed its check with its score, the chosen one
 * marked. Clicking another still picks it; the decision bar under each shot keeps it, redoes the
 * shot with a change, or fixes one thing in the chosen still. A shot whose stills all failed says
 * why, in the checker's words. */

import { AlertTriangle } from 'lucide-react'

import type { CampaignDetail, Decision, Media } from '../agentd/campaigns'
import { DecisionBar } from './DecisionBar'

export function StillsBoard({
  campaign,
  media,
  decisions,
  onDecide,
  disabled,
}: {
  campaign: CampaignDetail
  media: Media
  decisions: Record<string, Decision>
  onDecide: (shot: string, d: Decision) => void
  disabled?: boolean
}) {
  const purpose = Object.fromEntries(campaign.brief.shots.map((s) => [s.id, s.purpose]))
  return (
    <div className="shots">
      {campaign.shots.map((shot) => {
        const decision = decisions[shot.shot_id] || { kind: 'keep' }
        const picked = decision.kind === 'pick' ? decision.still : shot.still
        const stills = Object.entries(shot.stills).sort((a, b) => b[1] - a[1])
        return (
          <section key={shot.shot_id} className="shot">
            <header className="shot-head">
              <span className="shot-id">{shot.shot_id}</span>
              <span className="shot-purpose">{purpose[shot.shot_id] || ''}</span>
              <span className={`shot-status s-${shot.status.replace(/\s+/g, '-')}`}>{shot.status}</span>
            </header>

            {stills.length > 0 ? (
              <div className="tiles">
                {stills.map(([path, score]) => (
                  <button
                    key={path}
                    className={`tile-still${path === picked ? ' chosen' : ''}`}
                    disabled={disabled}
                    onClick={() =>
                      onDecide(shot.shot_id, path === shot.still ? { kind: 'keep' } : { kind: 'pick', still: path })
                    }
                    title={path}
                  >
                    <img src={media(path)} alt={`${shot.shot_id} still`} loading="lazy" />
                    <span className="score">{score}/10</span>
                    {path === picked && <span className="chosen-tag">Chosen</span>}
                  </button>
                ))}
              </div>
            ) : (
              <div className="shot-empty">
                <AlertTriangle size={15} /> No still passed.
                {shot.problems.length > 0 && (
                  <ul className="problems">
                    {shot.problems.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <DecisionBar
              value={decision}
              onChange={(d) => onDecide(shot.shot_id, d)}
              fixable={picked || undefined}
              disabled={disabled}
            />
          </section>
        )
      })}
    </div>
  )
}
