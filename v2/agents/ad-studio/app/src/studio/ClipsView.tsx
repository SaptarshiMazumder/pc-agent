/* The clips gate: each animated shot's clip beside the still it started from, with how its check
 * went — passed, failed (with the checker's problems), unchecked (the provider gave no last frame
 * to judge), or refused (the provider would not take the still, with its reason). */

import type { CampaignDetail, Decision, Media } from '../agentd/campaigns'
import { DecisionBar } from './DecisionBar'

const CHECK_LABEL: Record<string, string> = {
  passed: 'Check passed',
  failed: 'Check failed',
  unchecked: 'Not checked — watch it',
  refused: 'Refused by the provider',
}

export function ClipsView({
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
  const animated = campaign.shots.filter((s) => campaign.plan.animate.includes(s.shot_id))
  return (
    <div className="shots">
      {animated.map((shot) => (
        <section key={shot.shot_id} className="shot">
          <header className="shot-head">
            <span className="shot-id">{shot.shot_id}</span>
            <span className={`shot-status c-${shot.clip_check || 'none'}`}>
              {CHECK_LABEL[shot.clip_check] || shot.status}
            </span>
          </header>
          <div className="clip-row">
            {shot.clip ? (
              <video className="clip" src={media(shot.clip)} controls loop playsInline preload="metadata" />
            ) : (
              <div className="clip clip-missing">No clip</div>
            )}
            {shot.still && <img className="clip-still" src={media(shot.still)} alt="first frame" />}
          </div>
          {shot.problems.length > 0 && (
            <ul className="problems">
              {shot.problems.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          )}
          <DecisionBar
            value={decisions[shot.shot_id] || { kind: 'keep' }}
            onChange={(d) => onDecide(shot.shot_id, d)}
            disabled={disabled}
          />
        </section>
      ))}
    </div>
  )
}
