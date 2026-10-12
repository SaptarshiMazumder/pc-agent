/* The chat's story, editable: its direction (hook, occasion, who it's for, offer, tone, who is in it,
 * call to action) and its SHOT LIST — each row one image or video: what it shows, why, which product,
 * its words, how many, what shape, how long. Every edit is saved at once (story_update, free) and
 * makes a new version; Approve is the go (refused while a count, shape or length is unchosen, unless
 * the story's Auto is on); "Make it" asks the agent to make the approved story. */

import type { AgentdClient } from '@agentd/client'
import { ArrowDown, ArrowUp, Check, ChevronDown, ChevronRight, Clapperboard, Image as ImageIcon, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { approveStory, SHAPES, unmade, updateStory, type Story, type StoryShot } from '../agentd/stories'
import { useApp } from '../state/store'

/** A text field that saves when it loses focus (or on Enter), only when it changed. */
function Field({ value, onSave, placeholder, area }: { value: string; onSave: (v: string) => void; placeholder?: string; area?: boolean }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  const commit = () => draft.trim() !== value.trim() && onSave(draft.trim())
  return area ? (
    <textarea rows={2} value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)} onBlur={commit} />
  ) : (
    <input value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} />
  )
}

const DIRECTION: { key: keyof Story; label: string; placeholder: string }[] = [
  { key: 'hook', label: 'Hook', placeholder: 'The first words on screen' },
  { key: 'occasion', label: 'Occasion', placeholder: 'Diwali, wedding season, any time…' },
  { key: 'audience', label: 'Who it is for', placeholder: 'Who buys, who wears' },
  { key: 'offer', label: 'Offer', placeholder: 'Price, discount, deadline — only yours' },
  { key: 'tone', label: 'Tone', placeholder: 'Festive and warm, luxe…' },
  { key: 'cast', label: 'Who is in it', placeholder: 'A cast member, or none' },
  { key: 'language', label: 'Language', placeholder: 'English, Hinglish…' },
  { key: 'cta', label: 'Call to action', placeholder: 'DM to order, order on WhatsApp by…' },
  { key: 'caption_angle', label: 'Caption', placeholder: 'What the caption says' },
]

export function StoryPanel({
  client,
  story,
  busy,
  made,
  onSend,
  startCollapsed = false,
}: {
  client: AgentdClient | null
  story: Story
  busy: boolean
  /** Its campaigns exist already — "Make it" is done. */
  made: boolean
  onSend: (text: string) => void
  startCollapsed?: boolean
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [open, setOpen] = useState(!startCollapsed)
  const [error, setError] = useState('')
  const missing = story.shots.flatMap((s) => unmade(s).map((m) => `${s.id}: ${m}`))
  const save = (changes: Record<string, unknown>) => {
    if (!client) return
    setError('')
    updateStory(client, story.id, changes)
      .then(bump)
      .catch((e) => setError(String((e as Error)?.message || e)))
  }
  const saveShot = (shot: StoryShot, changes: Partial<StoryShot>) => save({ shot: { id: shot.id, ...changes } })
  const move = (i: number, by: -1 | 1) => {
    const ids = story.shots.map((s) => s.id)
    const j = i + by
    if (j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j], ids[i]]
    save({ order: ids })
  }

  return (
    <section className={`story-panel${open ? '' : ' closed'}`}>
      <header className="story-head" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="eyebrow-red">Story</span>
        <b className="story-title">{story.title}</b>
        <span className={`story-badge ${story.status}`}>
          v{story.version} · {story.status === 'approved' ? 'approved' : 'draft'}
        </span>
      </header>
      {open && (
        <div className="story-body">
          <div className="story-direction">
            {DIRECTION.map((f) => (
              <label key={f.key} className="step-choice">
                <span className="strip-label">{f.label}</span>
                <Field value={String(story[f.key] || '')} placeholder={f.placeholder} onSave={(v) => save({ [f.key]: v })} />
              </label>
            ))}
            <label className="step-choice">
              <span className="strip-label">Format</span>
              <select value={story.format} onChange={(e) => save({ format: e.target.value })}>
                <option value="reel">Reel</option>
                <option value="carousel">Carousel</option>
              </select>
            </label>
          </div>

          <div className="story-shots">
            <div className="story-shots-head">
              <span className="strip-label">Shot list — what gets made</span>
              <div className="seg-switch small" role="radiogroup" aria-label="Who chooses counts, shapes and lengths">
                {([false, true] as const).map((on) => (
                  <button key={String(on)} role="radio" aria-checked={story.auto_pick === on} className={story.auto_pick === on ? 'on' : ''} disabled={!client} onClick={() => save({ auto_pick: on })}>
                    {on ? 'Agent picks' : 'I choose'}
                  </button>
                ))}
              </div>
            </div>
            {story.shots.map((s, i) => (
              <div key={s.id} className={`story-shot${unmade(s).length && !story.auto_pick ? ' needed' : ''}`}>
                <span className="story-shot-n">{i + 1}</span>
                <span className="story-shot-kind" title={s.kind}>{s.kind === 'video' ? <Clapperboard size={14} /> : <ImageIcon size={14} />}</span>
                <div className="story-shot-main">
                  <Field value={s.purpose} placeholder="Why it is in the story" onSave={(v) => saveShot(s, { purpose: v })} />
                  <Field value={s.shows} placeholder="What it shows" onSave={(v) => saveShot(s, { shows: v })} area />
                  <Field value={s.text} placeholder="Words on screen (optional)" onSave={(v) => saveShot(s, { text: v })} />
                  {s.start_from && (
                    <span className="story-shot-from" title={s.start_from}>
                      Starts from your image: {s.start_from.split('/').pop()}
                      <button className="ref-x" onClick={() => saveShot(s, { start_from: '' })} title="Make it instead">
                        ×
                      </button>
                    </span>
                  )}
                </div>
                <div className="story-shot-size">
                  <select value={s.kind} onChange={(e) => saveShot(s, { kind: e.target.value as StoryShot['kind'], seconds: 0 })} title="Image or video">
                    <option value="image">Image</option>
                    <option value="video">Video</option>
                  </select>
                  <select value={s.product} onChange={(e) => saveShot(s, { product: e.target.value })} title="Which product">
                    <option value="">No product</option>
                    <option value="all">All products</option>
                    {story.products.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={1}
                    max={6}
                    defaultValue={s.count || ''}
                    key={`c${s.count}`}
                    placeholder={story.auto_pick && s.suggested_count ? `Auto: ${s.suggested_count}` : 'How many?'}
                    onBlur={(e) => Number(e.target.value || 0) !== s.count && saveShot(s, { count: Number(e.target.value || 0) })}
                  />
                  <select value={s.aspect} onChange={(e) => saveShot(s, { aspect: e.target.value })} title="Shape">
                    <option value="">{story.auto_pick && s.suggested_aspect ? `Auto: ${s.suggested_aspect}` : 'Shape…'}</option>
                    {SHAPES.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                  {s.kind === 'video' && (
                    <input
                      type="number"
                      min={2}
                      max={15}
                      defaultValue={s.seconds || ''}
                      key={`s${s.seconds}`}
                      placeholder={story.auto_pick && s.suggested_seconds ? `Auto: ${s.suggested_seconds}s` : 'Seconds?'}
                      onBlur={(e) => Number(e.target.value || 0) !== s.seconds && saveShot(s, { seconds: Number(e.target.value || 0) })}
                    />
                  )}
                </div>
                <div className="story-shot-tools">
                  <button className="ref-x" disabled={i === 0} onClick={() => move(i, -1)} title="Earlier">
                    <ArrowUp size={11} />
                  </button>
                  <button className="ref-x" disabled={i === story.shots.length - 1} onClick={() => move(i, 1)} title="Later">
                    <ArrowDown size={11} />
                  </button>
                  <button className="ref-x" onClick={() => save({ remove_shot: s.id })} title="Remove this shot">
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
            ))}
            <button className="ref-add" disabled={!client} onClick={() => save({ add_shot: { shows: '', purpose: 'new shot', kind: 'image', product: story.products[0] || '' } })}>
              <Plus size={11} /> Add a shot
            </button>
          </div>

          {error && <div className="studio-error">{error}</div>}
          <footer className="story-foot">
            <span className="page-note">
              {missing.length && !story.auto_pick ? `Still to choose — ${missing.join(' · ')}` : story.status === 'approved' ? 'Approved — the shot list is final for this version.' : 'Ready to approve.'}
            </span>
            {story.status === 'draft' ? (
              <button
                className="prime-btn"
                disabled={!client || busy || (missing.length > 0 && !story.auto_pick)}
                onClick={() => client && void approveStory(client, story.id).then(bump).catch((e) => setError(String((e as Error)?.message || e)))}
              >
                <Check size={14} /> Approve
              </button>
            ) : (
              !made && (
                <button className="prime-btn" disabled={busy} onClick={() => onSend(`Make the story ${story.id}.`)} title="The agent makes one campaign per product, one stage per shot">
                  <Sparkles size={14} /> Make it
                </button>
              )
            )}
          </footer>
        </div>
      )}
    </section>
  )
}
