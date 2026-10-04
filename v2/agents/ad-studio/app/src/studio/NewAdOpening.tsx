/* The chat side of a new ad: the recipes first, or a cast member. A click adds to the message and
 * the studio beside it offers the other choice. "Direct the look" adds the fields the brief keeps. */

import type { AgentdClient } from '@agentd/client'
import { Shirt } from 'lucide-react'

import { useApp } from '../state/store'
import { CastCards } from './CastCards'
import { RecipeCards } from './RecipeCards'
import { useStartChoice } from './useStartChoice'
import { useStartData } from './useStartData'

const LOOK = 'She wears … ; location … ; time of day … ; mood … '

export function NewAdOpening({ client, session }: { client: AgentdClient | null; session: string }) {
  const { recipes, cast, media, error } = useStartData(client)
  const { start, pickRecipe, pickCast, newCast } = useStartChoice(session)
  const append = useApp((s) => s.appendComposer)
  return (
    <div className="opening">
      <span className="opening-eyebrow">Ad Studio</span>
      <h2 className="opening-headline">What are we selling today?</h2>
      <p className="opening-blurb">Pick a recipe — or start with a cast member — then attach the product photos and send.</p>
      {error && <div className="studio-error">Could not load the recipes and cast: {error}</div>}
      <RecipeCards
        recipes={recipes}
        chosen={start.recipe}
        onPick={(key) => pickRecipe(key, recipes.find((r) => r.key === key)?.start_text)}
      />
      <span className="opening-eyebrow">Or start with a cast member</span>
      <CastCards cast={cast} media={media} chosen={start.cast} onPick={pickCast} onNew={newCast} />
      <button className="ref-add opening-look" onClick={() => append(LOOK)} title="Add the look's fields to your message">
        <Shirt size={12} /> Direct the look — outfit, place, light, mood
      </button>
    </div>
  )
}
