/* The recipes as cards: what each is for, its steps in order, whether it casts a model. Clicking
 * one starts (or completes) an ad with it. */

import { Check, Users } from 'lucide-react'

import type { Recipe } from '../agentd/campaigns'

export function RecipeCards({
  recipes,
  chosen = '',
  onPick,
  action = 'Use',
}: {
  recipes: Recipe[]
  chosen?: string
  onPick: (key: string) => void
  /** What the card's button says. */
  action?: string
}) {
  return (
    <div className="recipe-cards">
      {recipes.map((r) => (
        <button key={r.key} className={`recipe-card${chosen === r.key ? ' on' : ''}`} onClick={() => onPick(r.key)} title={r.covers}>
          <span className="recipe-card-head">
            <b>{r.key}</b>
            {chosen === r.key ? (
              <span className="recipe-tag on">
                <Check size={11} /> chosen
              </span>
            ) : (
              <span className="recipe-tag">{action}</span>
            )}
          </span>
          <span className="recipe-title">{r.title}</span>
          <span className="recipe-steps">
            {r.steps.map((s, i) => (
              <span key={s.id} className="recipe-step">
                {i > 0 && <span className="recipe-arrow">→</span>}
                {s.title}
              </span>
            ))}
          </span>
          <span className="recipe-meta">
            {r.needs_cast ? (
              <>
                <Users size={11} /> with a model
              </>
            ) : (
              'product only'
            )}
            {` · ${r.variants} images to pick from`}
          </span>
        </button>
      ))}
    </div>
  )
}
