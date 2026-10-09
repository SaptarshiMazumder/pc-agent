/* What every run of a campaign references: the cast member's sheet and the product photos. Opened
 * from the campaign header's chips; each picture opens full screen or is selected like any other. */

import { X } from 'lucide-react'

import type { CampaignDetail, Media } from '../agentd/campaigns'
import { MediaTileActions } from './MediaTileActions'

export function CampaignAssets({ campaign, media, onClose }: { campaign: CampaignDetail; media: Media; onClose: () => void }) {
  return (
    <div className="studio-strip">
      {campaign.cast && (
        <div className="strip-card">
          <span className="strip-label">Cast · {campaign.cast.name}</span>
          <div className="sheet-box">
            <img src={media(campaign.cast.sheet)} alt={campaign.cast.name} />
            <MediaTileActions
              item={{ path: campaign.cast.sheet, kind: 'image', campaign: campaign.campaign_id, shot: '', src: media(campaign.cast.sheet) }}
              title={`Cast · ${campaign.cast.name}`}
            />
          </div>
        </div>
      )}
      <div className="strip-card">
        <span className="strip-head">
          <span className="strip-label">Product · {campaign.product.category}</span>
          <button className="ref-x" onClick={onClose} title="Close" aria-label="Close">
            <X size={11} />
          </button>
        </span>
        <div className="strip-photos">
          {campaign.product.photos.map((p) => (
            <div key={p} className="sheet-box">
              <img src={media(p)} alt="product" />
              <MediaTileActions item={{ path: p, kind: 'image', campaign: campaign.campaign_id, shot: '', src: media(p) }} title="Product photo" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
