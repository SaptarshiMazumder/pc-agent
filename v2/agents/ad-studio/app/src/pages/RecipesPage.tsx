/* The recipes: what each is for and its default steps. "Use in a new ad" opens a new chat with the
 * recipe chosen — the studio then offers the cast. A recipe is only the default order: every step
 * can be re-run, skipped or added to once the ad is going. */

import type { AgentdClient } from '@agentd/client'

import { RecipeCards } from '../studio/RecipeCards'
import { useStartData } from '../studio/useStartData'

export function RecipesPage({
  client,
  onUse,
}: {
  client: AgentdClient | null
  onUse: (recipe: string, startText: string) => void
}) {
  const { recipes, error, loaded } = useStartData(client)
  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Recipes</h1>
        <p className="page-note">
          {loaded ? `${recipes.length} recipes` : 'Loading…'} — the default steps of an ad. Once it is going, any step can be run again,
          skipped, or added to.
        </p>
      </header>
      {error && <div className="studio-error">{error}</div>}
      <RecipeCards recipes={recipes} onPick={(key) => onUse(key, recipes.find((r) => r.key === key)?.start_text || '')} action="Use in a new ad" />
      <div className="recipe-covers">
        {recipes.map((r) => (
          <p key={r.key}>
            <b>{r.key}</b> — for {r.covers}
          </p>
        ))}
      </div>
    </div>
  )
}
