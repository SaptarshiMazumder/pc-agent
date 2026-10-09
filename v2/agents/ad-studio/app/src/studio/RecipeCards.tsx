/* The recipes as cards: the name, what it makes, what it is for, its stages in order, whether it
 * casts a model. Clicking one starts (or completes) an ad with it. */

import { Check } from 'lucide-react'

import { money, type Recipe } from '../agentd/campaigns'

/** What a recipe ends in: a clip, posters or stills, at its aspect ratio. */
function output(r: Recipe): string {
  const last = r.steps[r.steps.length - 1]?.action
  const kind = last === 'video' ? 'clip' : r.brief === 'poster' ? 'poster' : 'stills'
  return `${r.aspect_ratio} ${kind}`
}

export function RecipeCards({
  recipes,
  chosen = '',
  onPick,
}: {
  recipes: Recipe[]
  chosen?: string
  onPick: (key: string) => void
}) {
  return (
    <div className="recipe-cards">
      {recipes.map((r) => (
        <button key={r.key} className={`recipe-card${chosen === r.key ? ' on' : ''}`} onClick={() => onPick(r.key)} title={r.title} aria-pressed={chosen === r.key}>
          <span className="recipe-card-head">
            <b>{r.name}</b>
            <span className="recipe-out">{output(r)}</span>
          </span>
          <span className="recipe-covers-line">{r.covers}</span>
          <span className="recipe-steps">
            {r.steps.map((s, i) => (
              <span key={s.id} className="recipe-step">
                {i > 0 && <span className="recipe-arrow">→</span>}
                <span className="recipe-step-chip">{s.title}</span>
              </span>
            ))}
          </span>
          <span className="recipe-meta">
            {r.needs_cast ? 'With a model' : 'Product only'} · {r.variants} images per run · budget {money(r.budget_usd)}
          </span>
          {chosen === r.key && (
            <span className="recipe-check" aria-hidden="true">
              <Check size={12} strokeWidth={3} />
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
