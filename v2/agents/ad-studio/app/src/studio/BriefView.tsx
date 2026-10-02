/* The brief gate: the look the whole ad keeps, and each shot's scene, as the checklist fields the
 * prompts are composed from — so the user approves what will actually be asked for. */

import type { CampaignDetail } from '../agentd/campaigns'

const LOOK_ORDER = ['wardrobe', 'location', 'time_of_day', 'weather', 'mood']
const SHOT_ORDER = ['framing', 'pose', 'background', 'lighting', 'action', 'camera_move']
const label = (k: string) => k.replace(/_/g, ' ')

export function BriefView({ campaign }: { campaign: CampaignDetail }) {
  const { brief, planned, direction } = campaign
  const look = LOOK_ORDER.filter((k) => brief.look[k])
  return (
    <div className="brief">
      {(brief.concept || brief.hook) && (
        <div className="brief-lead">
          {brief.concept && <p className="brief-concept">{brief.concept}</p>}
          {brief.hook && (
            <p className="brief-hook">
              <span className="tag">Hook</span> {brief.hook}
            </p>
          )}
        </div>
      )}

      <div className="fields">
        {look.map((k) => (
          <div key={k} className={`field${direction[k] ? ' given' : ''}`} title={direction[k] ? 'Your direction' : 'The brief chose this'}>
            <span className="field-k">{label(k)}</span>
            <span className="field-v">{brief.look[k]}</span>
          </div>
        ))}
      </div>

      <div className="shot-cards">
        {brief.shots
          .filter((s) => planned.includes(s.id))
          .map((s) => (
            <div key={s.id} className="shot-card">
              <div className="shot-card-head">
                <span className="shot-id">{s.id}</span>
                <span className="shot-purpose">{s.purpose}</span>
                <span className="shot-dur">{s.duration_s}s</span>
              </div>
              {SHOT_ORDER.filter((k) => s.spec?.[k]).map((k) => (
                <div key={k} className="spec">
                  <span className="spec-k">{label(k)}</span>
                  <span className="spec-v">{s.spec[k]}</span>
                </div>
              ))}
            </div>
          ))}
      </div>
      {brief.caption && (
        <p className="brief-caption">
          <span className="tag">Caption</span> {brief.caption}
        </p>
      )}
    </div>
  )
}
