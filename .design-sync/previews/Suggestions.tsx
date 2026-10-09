/* Suggestions — the agent's "what next" chips, parsed from a ```suggest fence at the end of its
 * message (`label | prompt` per line, at most four). The label is the chip; the prompt is what
 * a click sends (shown as the chip's tooltip). They are offers, not a gate.
 *
 * Cells: chips offered after a finished render, the same set inert while a run is going
 * (`disabled`), and a single follow-up. Renders nothing for an empty list, so there is no
 * empty cell. Hover styling is interaction-only and not shown. */
import { Suggestions } from 'agent-app'

const noop = () => {}

const AFTER_RENDER = [
  { label: 'Make it a 5s clip', prompt: 'Animate the picked still into a 5 second 720×1280 clip with wan2.2, slow push-in.' },
  { label: 'Try four seeds', prompt: 'Run the same workflow with four new seeds so I can compare.' },
  { label: 'Sharper skin', prompt: 'Add the realism_skin_v2 LoRA at 0.6 and re-run at 1024×1536.' },
  { label: 'Save to Library', prompt: 'Save this workflow to my Library as "golden hour portrait".' },
]

export const Offered = () => (
  <div style={{ maxWidth: 640 }}>
    <Suggestions items={AFTER_RENDER} onPick={noop} />
  </div>
)

export const WhileRunning = () => (
  <div style={{ maxWidth: 640 }}>
    <Suggestions items={AFTER_RENDER.slice(0, 3)} onPick={noop} disabled />
  </div>
)

export const SingleFollowUp = () => (
  <div style={{ maxWidth: 640 }}>
    <Suggestions
      items={[{ label: 'Test it now', prompt: 'Run the workflow on Comfy Cloud and show me the result.' }]}
      onPick={noop}
    />
  </div>
)
