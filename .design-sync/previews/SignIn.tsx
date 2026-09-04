/* SignIn — the gate card. It positions itself `fixed` over the whole window, which escapes a
 * preview cell; the transformed wrapper makes this div its containing block (a CSS containing-
 * block rule, not a style opinion), so the card centers inside the cell. */
import { SignIn } from 'agent-app'

export const Card = () => (
  <div style={{ transform: 'translateZ(0)', height: 640, position: 'relative' }}>
    <SignIn product="Comfy Artchitect" onDone={() => {}} />
  </div>
)
