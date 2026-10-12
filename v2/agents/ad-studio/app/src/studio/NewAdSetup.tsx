/* The studio side of a new ad: the STORY first (a theme for the occasions coming up, or the user's
 * own), the products (from the catalogue), and the workspace (any other files it starts from). The
 * agent drafts the story and its shot list with the user, and picks the template that makes the
 * shots; choosing the template and the cast yourself is under "Advanced".
 *
 * Every click adds its sentence to the composer (useStartChoice) — the sentences the agent reads
 * (AGENTS.md, "Starting from the window") — and the chat beside it reads the choices back
 * (StartSummary); the workspace's files and tags ride with the first message (workspaceBlock). The
 * user still sends.
 *
 * A MADE CHOICE COLLAPSES to one line (ChoiceSummary) with Change, so the screen shows only what is
 * still left to decide. */

import type { AgentdClient } from '@agentd/client'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { useApp, type WorkspaceFile } from '../state/store'
import { AdWorkspace } from './AdWorkspace'
import { CastCards } from './CastCards'
import { ChoiceSummary } from './ChoiceSummary'
import { ProductPicker } from './ProductPicker'
import { RecipeCards } from './RecipeCards'
import { StoryPicker } from './StoryPicker'
import { useStartChoice } from './useStartChoice'
import { useStartData } from './useStartData'

const NO_FILES: WorkspaceFile[] = []

export function NewAdSetup({ client, session }: { client: AgentdClient | null; session: string }) {
  const { recipes, cast, media, error } = useStartData(client)
  const { start, pickRecipe, pickCast, pickScene, newCast, pickTheme, pickProducts } = useStartChoice(session)
  const workspace = useApp((s) => s.starts[session]?.workspace) ?? NO_FILES
  // A made choice is shown collapsed until Change reopens it.
  const [reopened, setReopened] = useState<{ recipe?: boolean; who?: boolean; theme?: boolean; products?: boolean }>({})
  const [advanced, setAdvanced] = useState(!!(start.recipe || start.cast))

  const recipe = recipes.find((r) => r.key === start.recipe)
  const scenes = recipe && !recipe.needs_cast ? recipe.scene_options : []
  // A cast member chosen first: only the templates that have a model in them.
  const offered = start.cast && !start.recipe ? recipes.filter((r) => r.needs_cast) : recipes
  const whoChosen = recipe && !recipe.needs_cast ? (scenes.length ? start.scene : '') : start.cast

  return (
    <div className="new-ad">
      <header className="new-ad-head">
        <span className="eyebrow-red">Start an ad</span>
        <h2>What are we making?</h2>
        <p className="page-note">
          Pick a story and your products, then send. The agent writes the story and its shot list with you — what gets made, how many, what
          shape — and nothing is made until you approve it.
        </p>
      </header>
      {error && <div className="studio-error">Could not load the templates and cast: {error}</div>}

      <section className="new-ad-section">
        {start.theme && !reopened.theme ? (
          <ChoiceSummary n={1} label="Story" value={start.theme === 'own' ? 'Your own story' : start.theme} onChange={() => setReopened((r) => ({ ...r, theme: true }))} />
        ) : (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${start.theme ? ' done' : ''}`}>1</span> The story
              <span className="new-ad-hint">What the ad says. A default to start from — change anything once it is drafted.</span>
            </h3>
            <StoryPicker
              client={client}
              products={start.products || []}
              chosen={start.theme || ''}
              onPick={(key) => {
                pickTheme(key)
                setReopened((r) => ({ ...r, theme: false }))
              }}
            />
          </>
        )}
      </section>

      <section className="new-ad-section">
        {(start.products || []).length > 0 && !reopened.products ? (
          <ChoiceSummary n={2} label="Products" value={`${start.products!.length} chosen`} onChange={() => setReopened((r) => ({ ...r, products: true }))} />
        ) : (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${(start.products || []).length ? ' done' : ''}`}>2</span> The products
              <span className="new-ad-hint">From your catalogue — set up once, picked for any story.</span>
            </h3>
            <ProductPicker
              client={client}
              chosen={start.products || []}
              onUse={(products) => {
                pickProducts(products.map((p) => ({ key: p.key, title: p.title })))
                setReopened((r) => ({ ...r, products: false }))
              }}
            />
          </>
        )}
      </section>

      <section className="new-ad-section">
        <h3 className="new-ad-h">
          <span className={`new-ad-n${workspace.length ? ' done' : ''}`}>3</span> Workspace
          <span className="new-ad-hint">Any other files it starts from — your own stills, a look to follow. Say what each is.</span>
        </h3>
        <AdWorkspace client={client} session={session} media={media} />
      </section>

      <button className="link-red new-ad-advanced" onClick={() => setAdvanced(!advanced)}>
        {advanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Advanced: choose the stages (template) and who is in it yourself
      </button>
      {advanced && (
        <>
      <section className="new-ad-section">
        {recipe && !reopened.recipe ? (
          <ChoiceSummary n={2} label="Template" value={recipe.name} onChange={() => setReopened((r) => ({ ...r, recipe: true }))} />
        ) : (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${start.recipe ? ' done' : ''}`}>2</span> The template
              <span className="new-ad-hint">The stages it runs. Any stage can be re-run, skipped or added to later.</span>
            </h3>
            <RecipeCards
              recipes={offered}
              chosen={start.recipe}
              onPick={(key) => {
                pickRecipe(key, recipes.find((r) => r.key === key)?.start_text)
                setReopened((r) => ({ ...r, recipe: false }))
              }}
            />
          </>
        )}
      </section>

      <section className="new-ad-section">
        {whoChosen && !reopened.who ? (
          <ChoiceSummary
            n={3}
            label={recipe && !recipe.needs_cast ? 'Scene' : 'Who is in it'}
            value={whoChosen === 'new' ? 'A new cast member' : whoChosen}
            onChange={() => setReopened((r) => ({ ...r, who: true }))}
          />
        ) : recipe && !recipe.needs_cast ? (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${start.scene ? ' done' : ''}`}>3</span> {scenes.length ? 'Which scene?' : 'Who is in it?'}
            </h3>
            {scenes.length ? (
              <>
                <div className="scene-chips">
                  {scenes.map((s) => (
                    <button
                      key={s}
                      className={`filter-chip scene-chip${start.scene === s ? ' on' : ''}`}
                      onClick={() => {
                        pickScene(s)
                        setReopened((r) => ({ ...r, who: false }))
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <p className="page-note">Or describe your own — the background, the surface, the light — in your message.</p>
              </>
            ) : (
              <p className="new-ad-none">This template has no model in it — nothing to choose here.</p>
            )}
          </>
        ) : (
          <>
            <h3 className="new-ad-h">
              <span className={`new-ad-n${start.cast ? ' done' : ''}`}>3</span> Who is in it?
            </h3>
            <CastCards
              cast={cast}
              media={media}
              chosen={start.cast}
              onPick={(name) => {
                pickCast(name)
                setReopened((r) => ({ ...r, who: false }))
              }}
              onNew={() => {
                newCast()
                setReopened((r) => ({ ...r, who: false }))
              }}
            />
          </>
        )}
      </section>
        </>
      )}
    </div>
  )
}
