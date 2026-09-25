/* The right half of the hero: a real pipeline at a glance — the selfie that went in, the street
 * post and the talking clip that came out, and a workflow chip that says it is one saved setup.
 *
 * DECORATION WITH A JOB. It shows the three things the page is selling in one glance — video,
 * a face held across outputs, and the workflow behind them — before a word of copy is read.
 * Hidden from screen readers: the headline and the sections below say all of it in text.
 */

import { LandingShot } from './LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'

export function LandingHeroShowcase(): JSX.Element {
  return (
    <div className="lp-show" aria-hidden="true">
      <div className="lp-show-float lp-show-a">
        <LandingShot src="marketing/selfie-face.webp" alt="" className="shot-fill" />
      </div>
      <div className="lp-show-float lp-show-b">
        <LandingShot src="marketing/selfie-street-post.webp" alt="" className="shot-fill" />
      </div>
      <div className="lp-show-card">
        <MediaKindTag kind="workflow" />
        <b>One selfie → influencer</b>
        <span>post + talking reel · one saved workflow</span>
      </div>
      <div className="lp-show-phone">
        <LandingShot src="marketing/talking-street-reel.mp4" alt="" className="shot-fill" />
        <div className="lp-show-cap">
          <MediaKindTag kind="video" />
          <b>Talking street reel</b>
          <span>9:16 · 8s · from the selfie on the left</span>
        </div>
      </div>
    </div>
  )
}

export default LandingHeroShowcase
