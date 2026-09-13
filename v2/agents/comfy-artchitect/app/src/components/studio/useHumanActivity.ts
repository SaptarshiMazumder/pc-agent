/* Is a human in this window? Visible tab, and typed or clicked within the last few minutes.
 *
 * THIS IS ONE OF THE THREE THINGS THAT KEEP A RENTED GPU ALIVE (the others: a run producing
 * events for the account, and the box itself being busy). It deliberately knows nothing about
 * tools, runs or the agent: a person reading a result and thinking is using the machine as
 * much as a person typing, and a person who walked away an hour ago with the tab open is not.
 *
 * Interaction is counted from real input events, never from mouse movement alone — a mouse
 * drifting under a hand on the desk is not a person in the chat. Visibility is required so a
 * background tab cannot keep a machine billing.
 */

import { useEffect, useState } from 'react'

/** Re-evaluated on this cadence; the answer only matters at heartbeat granularity. */
const TICK_MS = 15_000

export function useHumanActivity(windowMs = 10 * 60_000): boolean {
  const [active, setActive] = useState(true)

  useEffect(() => {
    let last = Date.now()
    const touch = () => {
      last = Date.now()
    }
    const events: (keyof DocumentEventMap)[] = ['pointerdown', 'keydown', 'wheel', 'touchstart']
    for (const ev of events) document.addEventListener(ev, touch, { passive: true })
    const compute = () =>
      setActive(document.visibilityState === 'visible' && Date.now() - last < windowMs)
    document.addEventListener('visibilitychange', compute)
    const t = setInterval(compute, TICK_MS)
    compute()
    return () => {
      for (const ev of events) document.removeEventListener(ev, touch)
      document.removeEventListener('visibilitychange', compute)
      clearInterval(t)
    }
  }, [windowMs])

  return active
}
