/* StarterPrompts — the four "ways in" chips under an empty chat's composer. They name the job
 * (an influencer, an ad, consistent angles, an animated still), not a tool, and SEED the
 * composer on click rather than sending. The set is fixed inside the component (no props but
 * `onPick`), so the cells vary the one thing that changes their layout: the width they get
 * under the composer (max 720px) — one row at full width, wrapping to two rows on a narrow
 * window, where each row still fills its line. Hover styling is interaction-only. */
import { StarterPrompts } from 'agent-app'

const noop = () => {}

export const FullWidth = () => (
  <div style={{ maxWidth: 760 }}>
    <StarterPrompts onPick={noop} />
  </div>
)

export const NarrowWindow = () => (
  <div style={{ width: 380 }}>
    <StarterPrompts onPick={noop} />
  </div>
)
