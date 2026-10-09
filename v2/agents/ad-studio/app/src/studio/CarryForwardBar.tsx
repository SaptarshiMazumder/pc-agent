/* The foot of a stage: what it hands on — its pick — and the way to the next stage. Following the
 * recipe is opening each stage and generating; this makes the next one a click away. On the last
 * stage it says so. The pick itself is changed in the results (a click on any of them). */

import { ArrowRight } from 'lucide-react'

import { isVideo, type CampaignDetail, type CampaignStep, type Media } from '../agentd/campaigns'

export function CarryForwardBar({
  campaign,
  step,
  media,
  onShow,
}: {
  campaign: CampaignDetail
  step: CampaignStep
  media: Media
  onShow: (step: string) => void
}) {
  const index = campaign.steps.findIndex((s) => s.id === step.id)
  const next = campaign.steps.slice(index + 1).find((s) => s.status !== 'skipped')
  const name = step.pick.split('/').pop() || step.pick

  return (
    <div className="carry-bar">
      {step.pick && (
        <span className="carry-thumb">{isVideo(step.pick) ? <video src={media(step.pick)} muted playsInline preload="metadata" /> : <img src={media(step.pick)} alt="" />}</span>
      )}
      <div className="carry-text">
        <span className="carry-main">
          {step.pick
            ? `${name} is picked`
            : step.action === 'brief'
              ? step.status === 'done'
                ? 'The brief is written'
                : 'No brief yet'
              : step.status === 'skipped'
                ? 'This stage is skipped'
                : 'Nothing picked yet'}
        </span>
        <span className="carry-sub">
          {step.pick
            ? next
              ? 'It goes on to the next stages. Change the pick any time.'
              : 'It is the one in the ad. Change the pick any time.'
            : step.action === 'brief' || step.status === 'skipped'
              ? ''
              : 'Click a result to pick it.'}
        </span>
      </div>
      {next ? (
        <button className="prime-btn carry-go" onClick={() => onShow(next.id)}>
          Continue to {next.title} <ArrowRight size={15} />
        </button>
      ) : (
        <span className="carry-last">Last stage</span>
      )}
    </div>
  )
}
