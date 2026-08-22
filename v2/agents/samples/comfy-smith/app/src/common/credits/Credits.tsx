/* Credits & billing — GIVE IT ITS OWN VIEW.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source.
 *
 * THIS FILE ARRIVING IS NOT THE JOB. Nothing renders it until you do, and validate_agent reports
 * UI_NO_CREDITS until something does — shipping it and never showing it is a credits page that
 * exists, validates, and is invisible to the person who ran out of credits.
 *
 * Render it from a nav entry beside Settings, not inside your settings screen. Topping up is what
 * a user comes looking for the moment a run stops; settings is where you go to change how the
 * thing works. agentd draws the same line, and validate_agent expects to find this rendered.
 *
 * Every agent with a window sells credits, so it arrives already written rather than as a rule
 * to remember. Running out of credits is the ONE failure a user can
 * fix themselves, and an agent that cannot take the top-up just stops working and says nothing —
 * the user has to already know a separate app exists, find it, and buy there. Nobody does.
 *
 * WHY IT IS A WRAPPER AND NOT A REACT COMPONENT TREE. The panel itself lives in the SDK, so this
 * agent shows the same screen as agentd and as every other agent, down to the byte. One shop, one
 * set of rules about money. A React re-implementation would be a second store — a second set of
 * idempotency keys, refusal messages and "has the money actually arrived yet" — in an app that
 * takes real money. What is on sale comes from the server's catalogue, so prices change without
 * releasing this app, and the payment disclosure is the rail's own sentence rather than a promise
 * hardcoded here.
 *
 * IT RENDERS NOTHING when this build has no accounts service or nobody is signed in, so it is safe
 * to mount unconditionally. Theme it with the `--wallet-*` CSS custom properties.
 */

import { mountCreditsPanel } from '@agentd/client'
import { useEffect, useRef } from 'react'

export default function Credits() {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = host.current
    if (!el) return
    let panel: { destroy(): void } | null = null
    let live = true

    void mountCreditsPanel({ mount: el })
      .then((p) => {
        // StrictMode mounts, unmounts and remounts in development. Without this the first panel
        // is orphaned inside a detached node and its balance listener never unsubscribes.
        if (live) panel = p
        else p.destroy()
      })
      .catch((e) => console.error('credits panel failed', e))

    return () => {
      live = false
      panel?.destroy()
    }
  }, [])

  return <div ref={host} />
}
