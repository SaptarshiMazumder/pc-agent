/* What a start button does: the sentence it adds to the composer, and what the chat has now
 * chosen. Every click APPENDS — the first choice starts the message, the second completes it —
 * so whatever the user typed stays. The studio reads the choices to offer the other one next. */

import { START } from '../agentd/campaigns'
import { useApp } from '../state/store'

const NONE: { recipe?: string; cast?: string; scene?: string } = {}

export function useStartChoice(session: string) {
  const start = useApp((s) => s.starts[session]) ?? NONE
  const append = useApp((s) => s.appendComposer)
  const setStart = useApp((s) => s.setStart)
  const first = !start.recipe && !start.cast

  return {
    start,
    pickRecipe: (key: string, extra = '') => {
      append(first ? START.recipe(key, extra) : START.addRecipe(key, extra))
      setStart(session, { recipe: key })
    },
    pickCast: (name: string) => {
      append(first ? START.cast(name) : START.addCast(name))
      setStart(session, { cast: name })
    },
    pickScene: (scene: string) => {
      append(START.scene(scene))
      setStart(session, { scene })
    },
    newCast: () => {
      // On its own it makes a cast member; after a recipe, one for this ad.
      append(first ? START.newCastAlone : START.newCast)
      setStart(session, { cast: 'new' })
    },
  }
}
