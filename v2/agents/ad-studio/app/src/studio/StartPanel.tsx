/* A chat with no campaign yet: starting an ad, either way round.
 *
 *   nothing chosen   the cast and the recipes, either first
 *   a recipe         who is in it — the cast, or a new member (skipped for a recipe with no model)
 *   a cast member    which recipe
 *   a recipe with scene choices and no model (product stills)   which scene
 *   both             ready: attach the product photos and send
 *
 * Every click adds a sentence to the composer (useStartChoice); the user still attaches the
 * photos and sends. */

import type { AgentdClient } from '@agentd/client'
import { Check, Paperclip } from 'lucide-react'

import { CastCards } from './CastCards'
import { RecipeCards } from './RecipeCards'
import { useStartChoice } from './useStartChoice'
import { useStartData } from './useStartData'

export function StartPanel({ client, session }: { client: AgentdClient | null; session: string }) {
  const { recipes, cast, media, error } = useStartData(client)
  const { start, pickRecipe, pickCast, pickScene, newCast } = useStartChoice(session)
  const recipe = recipes.find((r) => r.key === start.recipe)
  const needsCast = !recipe || recipe.needs_cast
  const scenes = recipe && !recipe.needs_cast ? recipe.scene_options || [] : []
  const ready = !!start.recipe && (!!start.cast || !needsCast) && (!scenes.length || !!start.scene)

  return (
    <div className="studio-empty">
      {error && <div className="studio-error">Could not load the recipes and cast: {error}</div>}

      {(start.recipe || start.cast) && (
        <div className="start-chosen">
          {start.recipe && (
            <span className="start-pill">
              <Check size={12} /> recipe <b>{start.recipe}</b>
            </span>
          )}
          {start.scene && (
            <span className="start-pill">
              <Check size={12} /> {start.scene}
            </span>
          )}
          {start.cast && (
            <span className="start-pill">
              <Check size={12} /> {start.cast === 'new' ? 'a new cast member' : <>cast <b>{start.cast}</b></>}
            </span>
          )}
        </div>
      )}

      {ready ? (
        <div className="start-ready">
          <h2>Ready.</h2>
          <p>
            <Paperclip size={14} /> Attach the product photos, add anything about the look (outfit, place, light, mood), and send.
          </p>
        </div>
      ) : (
        <>
          {!start.recipe && !start.cast && (
            <>
              <span className="eyebrow-red">Start an ad</span>
              <h2>Pick a recipe or a cast member.</h2>
              <p className="start-note">Either first — you'll be offered the other next. Each click adds to your message.</p>
            </>
          )}

          {!start.cast && needsCast && (
            <>
              <span className="eyebrow-red">{start.recipe ? 'Who is in it?' : 'Your cast'}</span>
              <CastCards cast={cast} media={media} onPick={pickCast} onNew={newCast} />
            </>
          )}

          {start.recipe && scenes.length > 0 && !start.scene && (
            <>
              <span className="eyebrow-red">Which scene?</span>
              <div className="scene-chips">
                {scenes.map((s) => (
                  <button key={s} className="filter-chip scene-chip" onClick={() => pickScene(s)}>
                    {s}
                  </button>
                ))}
              </div>
              <p className="start-note">Or describe your own — the background, the surface, the light — in your message.</p>
            </>
          )}

          {!start.recipe && (
            <>
              <span className="eyebrow-red">{start.cast ? 'Which recipe?' : 'Recipes'}</span>
              <RecipeCards
                recipes={start.cast ? recipes.filter((r) => r.needs_cast) : recipes}
                onPick={(key) => pickRecipe(key, recipes.find((r) => r.key === key)?.start_text)}
              />
            </>
          )}
        </>
      )}
    </div>
  )
}
