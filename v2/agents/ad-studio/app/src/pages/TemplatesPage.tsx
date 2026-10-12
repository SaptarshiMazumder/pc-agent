/* The ad templates: one per marketing use case — what it makes, its stages in order, and the choices
 * each stage asks you for. Manual by default: you make every choice; a template whose Auto is on
 * says so. "Use this template" opens a new chat with it chosen (the studio then asks for the rest).
 *
 * Not the Brand kit's post templates (TemplateGallery): those are designs for Instagram posts. */

import type { AgentdClient } from '@agentd/client'
import { ArrowRight } from 'lucide-react'

import { money, type Recipe } from '../agentd/campaigns'
import { useStartData } from '../studio/useStartData'

/** What a template ends in: a clip, posters or stills, at its shape. */
function output(r: Recipe): string {
  const last = r.steps[r.steps.length - 1]?.action
  const kind = last === 'video' ? 'clip' : r.brief === 'poster' ? 'posters' : 'stills'
  return `${r.aspect_ratio} ${kind}`
}

export function TemplatesPage({ client, onUse }: { client: AgentdClient | null; onUse: (r: Recipe) => void }) {
  const { recipes, error, loaded } = useStartData(client)

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Templates</h1>
        <p className="page-note">
          Each template is one kind of ad, made in stages. You make the choices each stage asks for — who, where, how many, the model —
          or switch Auto on in a campaign to let the agent fill what you leave empty.
        </p>
      </header>
      {error && <div className="studio-error">Could not load the templates: {error}</div>}
      {loaded && !recipes.length && !error && <p className="page-note">No templates yet.</p>}

      <div className="template-grid">
        {recipes.map((r) => (
          <article key={r.key} className="template-card">
            <header className="template-card-head">
              <b>{r.name}</b>
              <span className="recipe-out">{output(r)}</span>
            </header>
            <p className="template-summary">{r.summary || r.title}</p>
            <ol className="template-stages">
              {r.steps.map((s, i) => (
                <li key={s.id}>
                  <span className="start-stage-n">{i + 1}</span>
                  <span className="template-stage-title">{s.title}</span>
                  {s.settings.length > 0 && (
                    <span className="template-stage-choices">
                      {s.settings.map((t) => (
                        <span key={t.key} className={`template-choice${t.required ? ' required' : ''}`} title={t.help}>
                          {t.label}
                        </span>
                      ))}
                    </span>
                  )}
                </li>
              ))}
            </ol>
            <footer className="template-card-foot">
              <span className="recipe-meta">
                {r.needs_cast ? 'With a model' : 'Product only'} · budget {money(r.budget_usd)} · {r.auto_pick ? 'Auto on' : 'You choose'}
              </span>
              <button className="prime-btn" disabled={!client} onClick={() => onUse(r)}>
                Use this template <ArrowRight size={14} />
              </button>
            </footer>
          </article>
        ))}
      </div>
    </div>
  )
}
