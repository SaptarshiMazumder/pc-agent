/* The cast as cards, and a card to make a new member. Clicking one starts (or completes) an ad
 * with that person. */

import { Check, UserPlus } from 'lucide-react'

import type { CastMember, Media } from '../agentd/campaigns'
import { MediaTileActions } from './MediaTileActions'

export function CastCards({
  cast,
  media,
  chosen = '',
  onPick,
  onNew,
}: {
  cast: CastMember[]
  media: Media
  /** A member's name, or 'new'. */
  chosen?: string
  onPick: (name: string) => void
  onNew: () => void
}) {
  return (
    <div className="cast-pick">
      {cast.map((m) => (
        <button key={m.name} className={`cast-chip${chosen === m.name ? ' on' : ''}`} onClick={() => onPick(m.name)} title={m.description}>
          <span className="sheet-box">
            <img src={media(m.sheet)} alt={m.name} />
            <MediaTileActions item={{ path: m.sheet, kind: 'image', campaign: '', shot: '', src: media(m.sheet) }} title={`Cast · ${m.name}`} selectable={false} />
          </span>
          <span>
            {chosen === m.name && <Check size={11} />} {m.name}
          </span>
        </button>
      ))}
      <button className={`cast-chip new${chosen === 'new' ? ' on' : ''}`} onClick={onNew}>
        <UserPlus size={18} />
        <span>New cast member</span>
      </button>
    </div>
  )
}
