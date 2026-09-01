/* Blueprint — the create wizard as one page whose right half is the SPEC about to be written.
 *
 * WHAT IT REPLACES. StartModal's create path asked its two questions on two screens: name first,
 * then the window/template gallery. This page asks the same two questions side by side and shows
 * the consequence live — the exact directory and agent.toml `create_agent` will write, updating
 * as you type. Nothing is hypothetical: the tree lists only files the tool really writes, and
 * the TOML shows only keys it really sets.
 *
 * THE CALL IS THE OLD CALL, BYTE FOR BYTE. Create hands (name, window, template, seed) to the
 * same createAgent the modal used — same placeholder identity, same pendingScope dance, same
 * seed-as-first-message. This page changes where the questions sit, not what answering them does.
 *
 * "WHAT IT DOES" IS THE SEED, and says so. The design draws a description field driving the
 * spec; the mechanism that already exists for "text that shapes the agent" is the opening
 * message (HeroSuggestions have always seeded it). So the field is that — pre-filled by a
 * suggestion, sent as your first message — rather than a new create_agent parameter invented
 * for the page.
 *
 * MODEL / SCHEDULE / CAPABILITIES are drawn where the design puts them and DISABLED, titled
 * honestly: the tool accepts them, but wiring them changes what a click creates, which is a
 * feature decision, not a redesign one. They activate in a later slice, deliberately.
 *
 * THE SHAPE ROW IS THE TEMPLATES EXPORT plus Headless — the same single source the launchpad
 * shelf and the modal gallery read. No Workbench tile: no `_variants/workbench` exists, and a
 * tile for an unpickable template is a dead click.
 *
 * WHERE THE LIVE PREVIEWS WENT. The modal's gallery showed each template running in an iframe
 * before you commit. That job now belongs to the launchpad shelf (live thumbnails, eye badge →
 * the real preview) and, next slice, the full-screen template viewer. This page keeps the
 * choice; the looking happens one screen earlier.
 */

import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronLeft,
  Clock,
  Cpu,
  Eye,
  LayoutGrid,
  MessageSquare,
  Plus,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { slugOf } from '../lib/agentSlug'
import { TemplatePreviewLightbox } from './TemplatePreviewLightbox'
import { TEMPLATES } from './StartModal'

const SHAPE_ICONS: Record<string, ReactNode> = {
  chat: <MessageSquare size={16} />,
  dashboard: <LayoutGrid size={16} />,
}

export function Blueprint({
  seed,
  initialShape,
  onCreate,
  onClose,
}: {
  /** A starter prompt that opened this page — pre-fills "What it does", exactly as it seeded
   *  the modal. Still sent as the opening message, nothing else. */
  seed?: string
  /** A Shape already answered on the way in — the viewer's "Use this template", or the shelf's
   *  Headless card. Pre-picks the tile; it stays changeable, because arriving with an answer
   *  is not the same as being unable to change your mind. */
  initialShape?: string
  onCreate: (name: string, window: boolean, template: string, seed?: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [what, setWhat] = useState(seed || '')
  /** 'chat' | 'dashboard' | 'headless' — the one Shape answer, folding the modal's two
   *  questions (window? which template?) into a single pick. */
  const [shape, setShape] = useState(initialShape || 'chat')
  /** A template being LOOKED AT full size — separate from `shape` (the selection), because
   *  looking must never silently change what Create would build. */
  const [preview, setPreview] = useState<string | null>(null)

  const named = name.trim()
  const slug = slugOf(name)
  const windowed = shape !== 'headless'

  // Escape unwinds one layer at a time: the preview lightbox first, then the page. A page you
  // can only exit by finding the arrow traps the person who changed their mind.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      setPreview((was) => {
        if (was === null) onClose()
        return null
      })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = (): void => {
    if (!named) return
    onCreate(named, windowed, windowed ? shape : 'chat', what.trim() || undefined)
  }

  return (
    <div className="bp" role="dialog" aria-modal="true" aria-label="Create a new agent">
      <header className="bp-head">
        <button className="icon-btn" onClick={onClose} title="Back">
          <ChevronLeft size={17} />
        </button>
        <h2 className="bp-head-title">New agent</h2>
        <button className="icon-btn push-end" onClick={onClose} title="Close (Esc)">
          <X size={16} />
        </button>
      </header>

      <div className="bp-body">
        <div className="bp-form">
          <label className="bp-field">
            <span className="bp-label">Name</span>
            <span className="bp-name-row">
              <input
                className="bp-name"
                value={name}
                autoFocus
                placeholder="Recipe Box"
                spellCheck={false}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  // Enter used to advance to the gallery; on one page it advances the caret to
                  // the next question instead of submitting a half-answered form.
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    document.querySelector<HTMLTextAreaElement>('.bp-what')?.focus()
                  }
                }}
              />
              {slug && <code className="bp-slug">{slug}</code>}
            </span>
          </label>

          <label className="bp-field">
            <span className="bp-label">What it does</span>
            <textarea
              className="bp-what"
              value={what}
              rows={3}
              placeholder="Keep my recipes, import them from links I paste, and answer “what can I cook tonight”."
              onChange={(e) => setWhat(e.target.value)}
            />
            <span className="bp-hint">
              Sent as your first message — the conversation shapes the agent from it.
            </span>
          </label>

          <div className="bp-field">
            <span className="bp-label">Shape</span>
            {/* THE SAME CARDS AS EVERYWHERE ELSE — live thumbnails, the same eye-to-lightbox
                preview — because a shape question answered blind on this screen was already
                answered with evidence one screen earlier. Clicking a card SELECTS (this is the
                choosing screen); the eye badge is the looking, kept separate so looking never
                silently changes what Create would build. A pick carried in from the viewer or
                the shelf arrives visibly selected. */}
            <div className="bp-tpls">
              {TEMPLATES.map((t) => (
                <div
                  key={t.id}
                  className={`bp-tpl ${shape === t.id ? 'is-picked' : ''}`}
                  role="button"
                  tabIndex={0}
                  title={t.blurb}
                  onClick={() => setShape(t.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setShape(t.id)
                    }
                  }}
                >
                  <span className="bp-tpl-thumb">
                    <iframe
                      className="bp-tpl-frame"
                      src={`/template-previews/${t.id}/`}
                      title={`${t.label} thumbnail`}
                      tabIndex={-1}
                    />
                    <button
                      className="lp-tpl-eye"
                      title={`Preview ${t.label} full size`}
                      aria-label={`Preview ${t.label} full size`}
                      onClick={(e) => {
                        e.stopPropagation()
                        setPreview(t.id)
                      }}
                    >
                      <Eye size={14} />
                    </button>
                    {shape === t.id && (
                      <span className="bp-tpl-check">
                        <Check size={13} />
                      </span>
                    )}
                  </span>
                  <span className="bp-tpl-caption">
                    {SHAPE_ICONS[t.id]}
                    {t.label}
                  </span>
                </div>
              ))}
              <div
                className={`bp-tpl ${shape === 'headless' ? 'is-picked' : ''}`}
                role="button"
                tabIndex={0}
                title="No window — reached from chat or on a schedule"
                onClick={() => setShape('headless')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setShape('headless')
                  }
                }}
              >
                <span className="bp-tpl-thumb bp-tpl-thumb--plain">
                  <Clock size={20} />
                  {shape === 'headless' && (
                    <span className="bp-tpl-check">
                      <Check size={13} />
                    </span>
                  )}
                </span>
                <span className="bp-tpl-caption">
                  <Clock size={16} />
                  Headless
                </span>
              </div>
            </div>
          </div>

          {/* Drawn where the design puts them, INERT on purpose — see the header. */}
          <div className="bp-twocol">
            <div className="bp-field">
              <span className="bp-label">Model</span>
              <button className="bp-select" disabled title="Not wired yet — the daemon default applies">
                <Cpu size={15} />
                Daemon default
                <ChevronDown size={14} />
              </button>
            </div>
            <div className="bp-field">
              <span className="bp-label">Schedule</span>
              <button className="bp-select" disabled title="Not wired yet — no heartbeat is set">
                <Clock size={15} />
                None
                <ChevronDown size={14} />
              </button>
            </div>
          </div>

          <div className="bp-field">
            <span className="bp-label">Capabilities</span>
            <div className="bp-caps">
              <button className="bp-cap" disabled title="Not wired yet — tools are granted in the conversation">
                <Plus size={14} />
                add later, in the conversation
              </button>
            </div>
          </div>
        </div>

        <aside className="bp-spec">
          <div className="bp-spec-head">
            <span className="bp-spec-label">Will be written</span>
            <span className="bp-spec-count">{windowed ? '4 entries' : '2 files'}</span>
          </div>

          {/* ONLY what create_agent + the scaffolder really produce for THIS pick: the skeleton
              pair, plus app/ (source) and ui/ (the prebuilt window) when a window is chosen.
              No skills/, no AGENTS.md — this call does not write them. */}
          <pre className="bp-tree">
            <span className="bp-tree-dir">agents/{slug || '…'}/</span>
            {'\n'}
            {windowed ? '├─ ' : '├─ '}
            <span>agent.toml</span>
            {'\n'}
            {windowed ? '├─ ' : '└─ '}
            <span>IDENTITY.md</span>
            {windowed && (
              <>
                {'\n'}├─ <span className="bp-tree-dir">app/</span>
                {'\n'}└─ <span className="bp-tree-dir">ui/</span>
              </>
            )}
          </pre>

          <pre className="bp-toml">
            <span className="bp-toml-comment"># written by create_agent — the conversation refines it</span>
            {'\n'}name    = <span className="bp-toml-str">"{named || '…'}"</span>
            {'\n'}version = <span className="bp-toml-str">"1.0.0"</span>
            {windowed && (
              <>
                {'\n\n'}
                <span className="bp-toml-section">[app]</span>
                {'\n'}title = <span className="bp-toml-str">"{named || '…'}"</span>
                {'\n'}mode  = <span className="bp-toml-str">"window"</span>
                {'\n'}entry = <span className="bp-toml-str">"ui/index.html"</span>
              </>
            )}
          </pre>

          <div className="bp-spec-foot">
            <button className="prime-btn" disabled={!named} onClick={submit} title={named ? `Create ${slug}` : 'Name it first'}>
              Create
              <ArrowRight size={15} />
            </button>
          </div>
        </aside>
      </div>

      {/* Looking, full size, without leaving the form. "Use this template" here means SELECT —
          it fills the Shape answer and comes back; Create is still the only commitment. */}
      {preview && (
        <TemplatePreviewLightbox
          templateId={preview}
          onUse={(id) => {
            setShape(id)
            setPreview(null)
          }}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}
