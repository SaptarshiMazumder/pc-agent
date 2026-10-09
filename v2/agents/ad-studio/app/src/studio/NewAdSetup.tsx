/* The studio side of a new ad: three choices on one page — the product photos, the recipe, and who
 * is in it (or, for a product-only recipe with scene choices, the scene).
 *
 * Every click adds its sentence to the composer (useStartChoice) — the sentences the agent reads
 * (AGENTS.md, "Starting from the window") — and the chat beside it reads the choices back
 * (StartSummary). The photos go onto the chat message, as if attached there; the user still sends. */

import type { AgentdClient } from '@agentd/client'
import { Upload } from 'lucide-react'
import { useRef, useState } from 'react'

import { useApp } from '../state/store'
import { CastCards } from './CastCards'
import { RecipeCards } from './RecipeCards'
import { useStartChoice } from './useStartChoice'
import { useStartData } from './useStartData'

const NO_PENDING: never[] = []

export function NewAdSetup({
  client,
  session,
  onFiles,
}: {
  client: AgentdClient | null
  session: string
  /** Attach files to the chat's next message. */
  onFiles: (files: FileList | File[]) => void
}) {
  const { recipes, cast, media, error } = useStartData(client)
  const { start, pickRecipe, pickCast, pickScene, newCast } = useStartChoice(session)
  const pending = useApp((s) => s.sessions[session]?.pending) ?? NO_PENDING
  const pick = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const recipe = recipes.find((r) => r.key === start.recipe)
  const scenes = recipe && !recipe.needs_cast ? recipe.scene_options : []
  // A cast member chosen first: only the recipes that have a model in them.
  const offered = start.cast && !start.recipe ? recipes.filter((r) => r.needs_cast) : recipes

  return (
    <div className="new-ad">
      <header className="new-ad-head">
        <span className="eyebrow-red">Start an ad</span>
        <h2>What are we making?</h2>
        <p className="page-note">Three choices — then the agent writes the brief and works through the recipe's stages with you.</p>
      </header>
      {error && <div className="studio-error">Could not load the recipes and cast: {error}</div>}

      <section className="new-ad-section">
        <h3 className="new-ad-h">
          <span className={`new-ad-n${pending.length ? ' done' : ''}`}>1</span> The product
        </h3>
        <div
          className={`photo-drop${over ? ' over' : ''}`}
          onDragOver={(e) => {
            if (!Array.from(e.dataTransfer.types).includes('Files')) return
            e.preventDefault()
            e.stopPropagation()
            setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            if (!e.dataTransfer.files.length) return
            e.preventDefault()
            e.stopPropagation()
            setOver(false)
            onFiles(e.dataTransfer.files)
          }}
        >
          <button className="photo-drop-btn" onClick={() => pick.current?.click()}>
            <Upload size={18} />
            <span>{pending.length ? `${pending.length} attached to your message — add more` : 'Drop the product photos here, or click to choose'}</span>
          </button>
          <input
            ref={pick}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) onFiles(e.target.files)
              e.target.value = ''
            }}
          />
        </div>
      </section>

      <section className="new-ad-section">
        <h3 className="new-ad-h">
          <span className={`new-ad-n${start.recipe ? ' done' : ''}`}>2</span> The recipe
          <span className="new-ad-hint">The stages it runs. Any stage can be re-run, skipped or added to later.</span>
        </h3>
        <RecipeCards recipes={offered} chosen={start.recipe} onPick={(key) => pickRecipe(key, recipes.find((r) => r.key === key)?.start_text)} />
      </section>

      <section className="new-ad-section">
        {recipe && !recipe.needs_cast ? (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${start.scene ? ' done' : ''}`}>3</span> {scenes.length ? 'Which scene?' : 'Who is in it?'}
            </h3>
            {scenes.length ? (
              <>
                <div className="scene-chips">
                  {scenes.map((s) => (
                    <button key={s} className={`filter-chip scene-chip${start.scene === s ? ' on' : ''}`} onClick={() => pickScene(s)}>
                      {s}
                    </button>
                  ))}
                </div>
                <p className="page-note">Or describe your own — the background, the surface, the light — in your message.</p>
              </>
            ) : (
              <p className="new-ad-none">This recipe has no model in it — nothing to choose here.</p>
            )}
          </>
        ) : (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${start.cast ? ' done' : ''}`}>3</span> Who is in it?
            </h3>
            <CastCards cast={cast} media={media} chosen={start.cast} onPick={pickCast} onNew={newCast} />
          </>
        )}
      </section>
    </div>
  )
}
