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

/**
 * Each animation, with the box that makes it the same optical size as the others.
 *
 * WHY THIS IS A CONSTANT AND NOT MEASURED AT RUNTIME. These three were drawn on different canvases
 * (512, 1024 and 500 square) and each paints a different fraction of its own: `loading-3` puts a
 * 145×315 martini in the middle of a 500² artboard. Rendered as-is, one is a speck and another
 * fills the frame.
 *
 * The obvious fix — `getBBox()` on load — is wrong, and wrong in both directions. It returns the
 * geometry bounds of every descendant, counting shapes that are fully transparent on that frame
 * and shapes parked off-stage. It measured `loading-1` far too large (rendering it tiny) and
 * `loading-3` too small (rendering it oversized and clipped on the left).
 *
 * So the boxes below are the bounds of what each animation actually PAINTS, measured by rendering
 * it to a canvas and scanning the alpha channel across 24 frames of its loop. Each box is squared
 * around the painted centre with 8% air, so every animation fills the same extent along its
 * longest axis and nothing is stretched or clipped. See the README beside the JSON for how to
 * re-measure after swapping one.
 */
const REEL = [
  { data: one, viewBox: '-16 -51 547 547' },
  { data: two, viewBox: '137 122 782 782' },
  { data: three, viewBox: '82 27 340 340' },
]

/** The floor for how long one animation holds. The actual hold rounds UP to a whole number of
 *  loops, so a swap never lands mid-gesture. */
const MIN_HOLD_MS = 10000

/** Seconds of one loop. `op`/`ip` are frames, `fr` is frames per second. */
function loopSeconds(data: any): number {
  const frames = Number(data?.op || 0) - Number(data?.ip || 0)
  const fps = Number(data?.fr || 0)
  return frames > 0 && fps > 0 ? frames / fps : 0
}

/** How long to hold this animation: the first whole number of loops that reaches the floor.
 *  A 3s loop holds for 12s, a 9.2s loop for 18.3s — never cut half way through. */
function holdFor(data: any): number {
  const loop = loopSeconds(data)
  if (!loop) return MIN_HOLD_MS
  return Math.ceil(MIN_HOLD_MS / 1000 / loop) * loop * 1000
}

export function Thinking() {
  const [index, setIndex] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)

  // Each animation sets its own hold, so the timer is re-armed per step rather than run on one
  // fixed interval.
  useEffect(() => {
    const timer = setTimeout(
      () => setIndex((i) => (i + 1) % REEL.length),
      holdFor(REEL[index].data),
    )
    return () => clearTimeout(timer)
  }, [index])

  useEffect(() => {
    const host = boxRef.current
    if (!host) return
    const { data, viewBox } = REEL[index]
    let anim: AnimationItem | null = null
    try {
      anim = lottie.loadAnimation({
        container: host,
        renderer: 'svg',
        loop: true,
        autoplay: true,
        animationData: data,
      })
      // The SVG exists by the time DOMLoaded fires. Re-pointing the viewBox is what crops each
      // animation to what it actually paints; `meet` keeps the aspect ratio, so nothing stretches.
      anim.addEventListener('DOMLoaded', () => {
        const svg = host.querySelector('svg')
        if (!svg) return
        svg.setAttribute('viewBox', viewBox)
        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet')
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
