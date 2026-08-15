/* What the agent is doing, while it is doing it.
 *
 * A run can spend a minute inside one tool call with nothing to show — no text yet, no row
 * finished — and a still screen during that is indistinguishable from a hung one. This is the
 * "it is alive" signal, in the place the reply will land.
 *
 * THE ANIMATIONS ARE BUNDLED, not fetched. An agent window is served off the daemon at
 * `/apps/<id>/` and has to work on a machine with no internet; a loader that 404s while the agent
 * is mid-run would be the worst possible thing to be missing. The three `.lottie` files are ZIP
 * containers, so their animation JSON is extracted at authoring time and imported directly —
 * which also means there is no loading state for the loading indicator.
 *
 * The LIGHT player, deliberately: it drops expression and effect support, and none of these three
 * use either (they are plain shape layers). It is ~100 KB smaller than the full build.
 */

import lottie, { type AnimationItem } from 'lottie-web/build/player/lottie_light'
import { useEffect, useRef, useState } from 'react'
import one from '../assets/loading/loading-1.json'
import two from '../assets/loading/loading-2.json'
import three from '../assets/loading/loading-3.json'

const REEL = [one, two, three]

/** How long each animation holds before the next one takes over. Long enough to read as a loop
 *  rather than a flicker, short enough that a slow tool call does not feel frozen. */
const HOLD_MS = 4000

export function Thinking() {
  const [index, setIndex] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)

  // Advance the reel. Mounting starts it at the first animation, so every run opens the same way.
  useEffect(() => {
    const timer = setInterval(() => setIndex((i) => (i + 1) % REEL.length), HOLD_MS)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    const host = boxRef.current
    if (!host) return
    let anim: AnimationItem | null = null
    try {
      anim = lottie.loadAnimation({
        container: host,
        renderer: 'svg',
        loop: true,
        autoplay: true,
        animationData: REEL[index],
      })
    } catch (e) {
      // A broken animation must never take the conversation down with it. Reported, not swallowed:
      // the row simply stays empty and the console says why.
      console.warn('[thinking] could not play animation', index, e)
    }
    return () => {
      anim?.destroy()
      // destroy() detaches its own SVG, but a failed load can leave a partial one behind.
      host.replaceChildren()
    }
  }, [index])

  return (
    <div className="thinking" aria-live="polite" aria-label="working">
      <div className="thinking-art" ref={boxRef} />
    </div>
  )
}
