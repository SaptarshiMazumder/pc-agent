/* The chat side of a new ad: what the choices in the studio have made so far — the recipe and its
 * stages, who is in it, the scene — and what is left before sending. The choices themselves are
 * made in the studio (NewAdSetup); each one has already added its sentence to the composer, so
 * this only reads them back. "Direct the look" adds the fields the brief keeps. */

import type { AgentdClient } from '@agentd/client'
import { Paperclip, Shirt } from 'lucide-react'

import { money } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { useStartChoice } from './useStartChoice'
import { useStartData } from './useStartData'

const LOOK = 'She wears … ; location … ; time of day … ; mood … '

export function StartSummary({ client, session }: { client: AgentdClient | null; session: string }) {
  const { recipes } = useStartData(client)
  const { start } = useStartChoice(session)
  const append = useApp((s) => s.appendComposer)
  const recipe = recipes.find((r) => r.key === start.recipe)
  const needsCast = !recipe || recipe.needs_cast
  const scenes = recipe && !recipe.needs_cast ? recipe.scene_options : []
  const ready = !!start.recipe && (!!start.cast || !needsCast) && (!scenes.length || !!start.scene)

  return (
    <div className="start-summary">
      <p className="start-summary-intro">
        Pick in the studio — or just tell me: attach the product photos and say who and where.
      </p>

      <div className="start-card">
        <span className="start-card-title">Your ad, so far</span>

        <div className="start-row">
          <span className="strip-label">Recipe{recipe ? ` · ${recipe.name}` : ''}</span>
          {recipe ? (
            <ol className="start-stages">
              {recipe.steps.map((s, i) => (
                <li key={s.id}>
                  <span className="start-stage-n">{i + 1}</span>
                  {s.title}
                </li>
              ))}
            </ol>
          ) : (
            <span className="start-empty">Not chosen yet</span>
          )}
        </div>

        <div className="start-row">
          <span className="strip-label">Cast</span>
          <span className={start.cast || (recipe && !needsCast) ? 'start-value' : 'start-empty'}>
            {start.cast === 'new' ? 'A new cast member' : start.cast || (recipe && !needsCast ? 'No model — the product alone' : 'Not chosen yet')}
          </span>
        </div>

        {(scenes.length > 0 || start.scene) && (
          <div className="start-row">
            <span className="strip-label">Scene</span>
            <span className={start.scene ? 'start-value' : 'start-empty'}>{start.scene || 'Not chosen yet'}</span>
          </div>
        )}

        {recipe && (
          <div className="start-row inline">
            <span className="strip-label">Budget</span>
            <span className="start-value mono">{money(recipe.budget_usd)}</span>
          </div>
        )}
      </div>

      {ready && (
        <p className="start-ready-note">
          <Paperclip size={13} /> Attach the product photos, add anything about the look, and send.
        </p>
      )}

      <button className="ref-add" onClick={() => append(LOOK)} title="Add the look's fields to your message">
        <Shirt size={12} /> Direct the look — outfit, place, light, mood
      </button>
    </div>
  )
}
