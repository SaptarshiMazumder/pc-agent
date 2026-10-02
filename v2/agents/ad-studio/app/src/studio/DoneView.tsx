/* A finished campaign: the deliverables in shot order — the chosen still and, where there is one,
 * the clip — each one a link to the full file, and the caption to post with them. */

import { Download } from 'lucide-react'

import type { CampaignDetail, Media } from '../agentd/campaigns'

export function DoneView({ campaign, media }: { campaign: CampaignDetail; media: Media }) {
  return (
    <div className="done">
      <div className="deliverables">
        {campaign.shots.map((s) => (
          <figure key={s.shot_id} className="deliverable">
            {s.clip ? (
              <video src={media(s.clip)} controls loop playsInline preload="metadata" />
            ) : s.still ? (
              <img src={media(s.still)} alt={s.shot_id} />
            ) : (
              <div className="clip-missing">{s.status}</div>
            )}
            <figcaption>
              <span className="shot-id">{s.shot_id}</span>
              {s.still && (
                <a href={media(s.still)} target="_blank" rel="noreferrer" title="Open the still">
                  <Download size={13} /> still
                </a>
              )}
              {s.clip && (
                <a href={media(s.clip)} target="_blank" rel="noreferrer" title="Open the clip">
                  <Download size={13} /> clip
                </a>
              )}
            </figcaption>
          </figure>
        ))}
      </div>
      {campaign.brief.caption && (
        <p className="brief-caption">
          <span className="tag">Caption</span> {campaign.brief.caption}
        </p>
      )}
    </div>
  )
}
