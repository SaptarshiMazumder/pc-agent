/* The deliverables: each image and video step's pick, in checklist order, each one a link to save
 * the file, and the caption to post with them. Shown once every step is done or skipped — and the
 * steps stay open beside it, so anything can still be remade. */

import { Download } from 'lucide-react'

import { isVideo, type CampaignDetail, type Media } from '../agentd/campaigns'
import { MediaTileActions } from './MediaTileActions'

export function DoneView({ campaign, media }: { campaign: CampaignDetail; media: Media }) {
  const picks = campaign.steps.filter((s) => (s.action === 'images' || s.action === 'video') && s.pick)
  if (!picks.length) return null
  return (
    <div className="done">
      <span className="strip-label">Deliverables</span>
      <div className="deliverables">
        {picks.map((s) => {
          const video = isVideo(s.pick)
          return (
            <figure key={s.id} className="deliverable">
              <div className="deliverable-media">
                {video ? <video src={media(s.pick)} controls loop playsInline preload="metadata" /> : <img src={media(s.pick)} alt={s.title} />}
                <MediaTileActions
                  item={{ path: s.pick, kind: video ? 'video' : 'image', campaign: campaign.campaign_id, shot: s.id, src: media(s.pick) }}
                  title={`${s.title} · ${s.pick.split('/').pop()}`}
                />
              </div>
              <figcaption>
                <span className="shot-purpose">{s.title}</span>
                <a href={media(s.pick)} download={s.pick.split('/').pop()} title="Save the file">
                  <Download size={13} /> save
                </a>
              </figcaption>
            </figure>
          )
        })}
      </div>
      {campaign.brief?.caption && (
        <p className="brief-caption">
          <span className="tag">Caption</span> {campaign.brief.caption}
        </p>
      )}
    </div>
  )
}
