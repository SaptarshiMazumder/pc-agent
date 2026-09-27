/* The Templates page — whole setups we made, ready to reuse.
 *
 * WHAT A CARD IS. The landing page's "what goes in → what comes out" format, shrunk onto a
 * stage at the top of a card — the same shots (LandingShot, lazy video posters) and media tags —
 * with what the template makes and needs, and one button, underneath. Cards sit in a grid at a
 * readable width rather than stretching across the screen.
 *
 * WHAT "USE THIS TEMPLATE" DOES. The shipped template is installed into the person's Library
 * (installSuggestedTemplate — once, and again only when the app ships a newer copy), then a new
 * chat opens with the template's brief in the box, unsent. From there it is an ordinary Library
 * template: the agent brings it in with template_use. There is no second path to maintain.
 *
 * THE CATALOGUE IS DATA (public/suggested-templates/index.json + one folder per template), so
 * adding a template is files, not code.
 */

import { ArrowRight, LayoutTemplate, Loader2 } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { LibraryItem } from '../../agentd/library'
import {
  installSuggestedTemplate,
  readSuggestedCatalogue,
  readSuggestedManifest,
  type SuggestedTemplate,
  type TemplateManifest,
} from '../../agentd/library-template'
import { LandingShot } from '../landing/LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'

import './templates.css'

export function SuggestedTemplatesPage({
  client,
  onUse,
}: {
  client: AgentdClient | undefined
  /** The installed Library template: a new chat with its brief. */
  onUse: (item: LibraryItem) => void
}) {
  const [catalogue, setCatalogue] = useState<SuggestedTemplate[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    readSuggestedCatalogue()
      .then((c) => alive && setCatalogue(c))
      .catch((e) => alive && setError(String((e as Error)?.message || e)))
    return () => {
      alive = false
    }
  }, [])

  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">Templates</h1>
          <p className="page-sub">
            Whole setups, ready to reuse — add your own photos and run. Each one is every workflow, installer and
            input it needs; change anything once it is in your chat.
          </p>
        </div>
      </header>
      <div className="tp-page">
        {error && <p className="lib-error">{error}</p>}
        {!catalogue && !error && (
          <div className="lib-loading" role="status">
            <Loader2 className="ld-spin" size={20} strokeWidth={1.8} />
            <span>Loading templates…</span>
          </div>
        )}
        {catalogue?.map((entry) => (
          <SuggestedTemplateCard key={entry.slug} entry={entry} client={client} onUse={onUse} />
        ))}
      </div>
    </>
  )
}

function SuggestedTemplateCard({
  entry,
  client,
  onUse,
}: {
  entry: SuggestedTemplate
  client: AgentdClient | undefined
  onUse: (item: LibraryItem) => void
}) {
  const [manifest, setManifest] = useState<TemplateManifest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    readSuggestedManifest(entry.slug)
      .then((m) => alive && setManifest(m))
      .catch((e) => alive && setError(String((e as Error)?.message || e)))
    return () => {
      alive = false
    }
  }, [entry.slug])

  const use = async (): Promise<void> => {
    if (!client) return
    setBusy(true)
    setError('')
    try {
      onUse(await installSuggestedTemplate(client, entry))
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  // THE STAGE: what goes in, then what comes out, small enough to sit in a card. A template
  // whose showcase media does not exist yet shows its own thumbnail as the result.
  const show = entry.showcase
  return (
    <article className="tp-card">
      <div className="tp-stage">
        {show ? (
          <>
            <div className="tp-ins">
              {show.inputs.map((s) => (
                <figure key={s.src} className="tp-shot tp-in">
                  <LandingShot src={s.src} alt={s.label} className="shot-fill" />
                  <figcaption>{s.label}</figcaption>
                </figure>
              ))}
            </div>
            {show.outputs.map((s) => (
              <Fragment key={s.src}>
                <span className="tp-arrow" aria-hidden="true">
                  <ArrowRight size={14} strokeWidth={2.4} />
                </span>
                <figure className="tp-shot tp-out">
                  <LandingShot src={s.src} alt={s.label} className="shot-fill" />
                  {s.kind !== 'input' && (
                    <span className="tp-tag">
                      <MediaKindTag kind={s.kind} over />
                    </span>
                  )}
                  <figcaption>{s.label}</figcaption>
                </figure>
              </Fragment>
            ))}
          </>
        ) : (
          manifest?.thumbnail && (
            <figure className="tp-shot tp-out tp-solo">
              <img className="shot-fill" src={`suggested-templates/${entry.slug}/${manifest.thumbnail}`} alt="" loading="lazy" />
              <figcaption>Result</figcaption>
            </figure>
          )
        )}
      </div>
      <div className="tp-body">
        <h3 className="tp-title">{entry.title}</h3>
        <p className="tp-prompt">&ldquo;{entry.prompt}&rdquo;</p>
        {manifest?.description && <p className="tp-desc">{manifest.description}</p>}
        {manifest && (
          <div className="tp-chips">
            <span className="tp-chip">
              {manifest.steps.length} workflow{manifest.steps.length === 1 ? '' : 's'}
            </span>
            {manifest.inputs.map((i) => (
              <span key={i.role} className="tp-chip is-input" title={i.what}>
                @{i.role}
              </span>
            ))}
          </div>
        )}
        {error && <p className="lib-error">{error}</p>}
        <button type="button" className="wp-btn is-primary tp-use" disabled={!client || !manifest || busy} onClick={() => void use()}>
          {busy ? <Loader2 size={14} strokeWidth={1.9} className="ld-spin" /> : <LayoutTemplate size={14} strokeWidth={1.9} />}
          {busy ? 'Setting up…' : 'Use this template'}
        </button>
      </div>
    </article>
  )
}

export default SuggestedTemplatesPage
