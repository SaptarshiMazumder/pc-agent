/* What a Save/Add to Library button says, press by press: Saving… → Saved ✓ / Already in
 * Library → back to itself.
 *
 * ONE HOOK FOR EVERY SAVE BUTTON (the Workflow tab, an Outputs tile, the file list, the chips
 * under a run, the Gallery), keyed by whatever the screen saves — a workflow's name, a file's
 * path — so several on one screen keep their own state. A press answered nothing visible
 * before; now the button itself says what happened, for a moment, and the Library lights up
 * (flashLibrary, done by whoever performs the save).
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import type { LibrarySaveOutcome } from '../../agentd/library'

export type SaveState = 'idle' | 'saving' | 'saved' | 'already'

/** How long "Saved ✓" / "Already in Library" stays on the button. */
const SETTLE_MS = 2500

export function useSaveFeedback(): {
  stateOf: (key: string) => SaveState
  run: (key: string, save: () => Promise<LibrarySaveOutcome>) => Promise<LibrarySaveOutcome>
} {
  const [states, setStates] = useState<Record<string, SaveState>>({})
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  useEffect(() => {
    const t = timers.current
    return () => Object.values(t).forEach(clearTimeout)
  }, [])

  const put = useCallback((key: string, state: SaveState) => {
    setStates((prev) => ({ ...prev, [key]: state }))
  }, [])

  const run = useCallback(
    async (key: string, save: () => Promise<LibrarySaveOutcome>) => {
      clearTimeout(timers.current[key])
      put(key, 'saving')
      try {
        const outcome = await save()
        put(key, outcome.state)
        timers.current[key] = setTimeout(() => put(key, 'idle'), SETTLE_MS)
        return outcome
      } catch (e) {
        put(key, 'idle')
        throw e
      }
    },
    [put],
  )

  const stateOf = useCallback((key: string) => states[key] || 'idle', [states])
  return { stateOf, run }
}

/** The button's words for a state; `idle` is what it says at rest. */
export function saveLabel(state: SaveState, idle: string): string {
  if (state === 'saving') return 'Saving…'
  if (state === 'saved') return 'Saved ✓'
  if (state === 'already') return 'Already in Library'
  return idle
}
