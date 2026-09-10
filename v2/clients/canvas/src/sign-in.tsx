/** The shared agent sign-in card, mountable by an app that uses the canvas bundle. */
import { createRoot } from 'react-dom/client'

import SignIn from '../../../agents/agent-builder/skills/build-agent/templates/_common/auth/SignIn'

export function mountSignIn(
  el: HTMLElement,
  opts: { product?: string; onDone(): void },
): { unmount(): void } {
  const root = createRoot(el)
  root.render(<SignIn product={opts.product} onDone={opts.onDone} />)
  return { unmount: () => root.unmount() }
}
