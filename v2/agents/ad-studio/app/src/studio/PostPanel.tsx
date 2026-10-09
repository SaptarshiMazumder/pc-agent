/* The post this chat is making: its slides in order (words, clip speed and fades), the caption and
 * hashtags — editable here (Save is free and instant) or in the chat. Render sends the agent the
 * exact call; the files then show here in order, with the caption to copy and the zip. */

import type { AgentdClient } from '@agentd/client'
import { Clapperboard, Copy, Download, ExternalLink, Globe, Loader2, Palette, Save, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import {
  canvaCommand,
  designCommand,
  KEEP_REFERENCES,
  listReferences,
  listTemplates,
  NO_REFERENCE,
  openCanva,
  renderCommand,
  updatePost,
  type DesignProgress,
  type DesignReference,
  type DesignTemplate,
  type Post,
  type Slide,
} from '../agentd/posts'
import { useApp } from '../state/store'
import { DesignProgressBar } from './DesignProgressBar'
import { SlideEditor } from './SlideEditor'
import { ViewableMedia, viewerItem } from './ViewableMedia'

export function PostPanel({
  client,
  post,
  media,
  progress,
  running,
  onSend,
}: {
  client: AgentdClient | null
  post: Post
  media: Media
  /** The post's last design run (null when it was never designed) — shown while it moves. */
  progress: DesignProgress | null
  running: boolean
  onSend: (text: string) => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [slides, setSlides] = useState<Slide[]>(post.slides)
  const [caption, setCaption] = useState(post.caption)
  const [tags, setTags] = useState(post.hashtags.map((h) => `#${h}`).join(' '))
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [templates, setTemplates] = useState<DesignTemplate[]>([])
  const [template, setTemplate] = useState('')
  const [references, setReferences] = useState<DesignReference[]>([])
  const [reference, setReference] = useState(KEEP_REFERENCES)
  useEffect(() => {
    if (!client) return
    Promise.all([listTemplates(client), listReferences(client)])
      .then(([t, r]) => {
        setTemplates(t.templates)
        setReferences(r.references)
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client])
  const key = JSON.stringify([post.slides, post.caption, post.hashtags])
  useEffect(() => {
    setSlides(post.slides)
    setCaption(post.caption)
    setTags(post.hashtags.map((h) => `#${h}`).join(' '))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  const hashtags = tags.split(/\s+/).map((t) => t.replace(/^#/, '')).filter(Boolean)
  const dirty = JSON.stringify([slides, caption, hashtags]) !== key

  const save = async () => {
    if (!client) return
    setSaving(true)
    setError('')
    try {
      await updatePost(client, post.slug, { slides, caption, hashtags })
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setSaving(false)
    }
  }
  const move = (i: number, by: -1 | 1) => {
    const next = [...slides]
    ;[next[i], next[i + by]] = [next[i + by], next[i]]
    setSlides(next)
  }
  const fullCaption = post.caption + (post.hashtags.length ? '\n\n' + post.hashtags.map((h) => `#${h}`).join(' ') : '')
  const zip = `posts/${post.slug}/${post.slug}.zip`
  // A render reuses the file names: the time it was made keeps the browser from showing the last one.
  const fresh = (f: string) => `${media(f)}&v=${post.rendered_at}`
  // While a slide is being designed, its thumbnail is the latest round's render (it changes each round).
  const live = progress?.active ? progress.slides : {}
  // A clip slide plays — its rendered video (words and motion over the clip) when it has one,
  // else the clip itself; a design's preview is one still frame and would hide that it moves.
  const renderedClip = (i: number) => post.rendered.find((f) => f.endsWith(`/${String(i + 1).padStart(2, '0')}.mp4`))
  const slideViews = slides.map((s, i) => {
    const now = live[String(i + 1)]?.preview
    const title = `Slide ${i + 1}${s.note ? ` · ${s.note}` : ''}`
    if (now) return viewerItem(`${media(now)}&v=${progress!.updated}`, now, title)
    if (s.kind === 'video') {
      const out = renderedClip(i)
      return out ? viewerItem(fresh(out), out, title) : viewerItem(media(s.item), s.item, title)
    }
    return viewerItem(media(s.preview || s.item), s.preview || s.item, title)
  })
  const fileViews = post.rendered.map((f, i) => viewerItem(fresh(f), f, `${String(i + 1).padStart(2, '0')} · ${f.split('/').pop()}`))

  return (
    <div className="post-panel">
      <header className="studio-head">
        <div className="studio-title">
          <span className="eyebrow-red">Instagram {post.format}</span>
          <h2>{post.name}</h2>
          <span className="studio-sub">
            {slides.length} slides · from collection {post.collection}
          </span>
        </div>
      </header>
      {error && <div className="studio-error">{error}</div>}
      {progress?.active && <DesignProgressBar progress={progress} />}

      <div className="model-pick">
        <label className="model-field grow">
          <span className="strip-label">Follow</span>
          <select value={reference} onChange={(e) => setReference(e.target.value)}>
            <option value={KEEP_REFERENCES}>{references.length ? `References — the best of ${references.length}` : 'References — none yet'}</option>
            <option value={NO_REFERENCE}>No reference</option>
            {references.map((r) => (
              <option key={r.slug} value={r.slug}>
                {r.name}
                {r.suits.length ? ` — ${r.suits.join(', ')}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="model-field grow">
          <span className="strip-label">Design from</span>
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">The agent's own design</option>
            {templates.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.name}
                {t.origin === 'saved' ? ' (yours)' : ''} — {t.kind}
              </option>
            ))}
          </select>
        </label>
        <button
          className="prime-btn gen-go"
          disabled={running || dirty || !client || !!progress?.active}
          title={progress?.active ? 'Slides are being designed' : dirty ? 'Save your changes first' : 'Design every slide as a professional Instagram ad (model calls only)'}
          onClick={() => onSend(designCommand(post, [], '', template, reference))}
        >
          <Sparkles size={14} /> Design slides
        </button>
      </div>

      <div className="slide-list">
        {slides.map((s, i) => (
          <SlideEditor
            key={`${s.item}:${i}`}
            n={i + 1}
            slide={s}
            views={slideViews}
            status={
              // while a run moves: where each slide is; after it: the slides the art director still had notes on, or that failed
              progress?.active
                ? live[String(i + 1)]
                : progress?.slides[String(i + 1)] && (progress.slides[String(i + 1)].problems.length || progress.slides[String(i + 1)].state !== 'done')
                  ? progress.slides[String(i + 1)]
                  : undefined
            }
            first={i === 0}
            last={i === slides.length - 1}
            onChange={(next) => setSlides(slides.map((x, k) => (k === i ? next : x)))}
            onMove={(by) => move(i, by)}
            onRemove={() => setSlides(slides.filter((_, k) => k !== i))}
            onRedesign={(notes) => onSend(designCommand(post, [i + 1], notes, ''))}
            busy={running || dirty || !client || !!progress?.active}
          />
        ))}
      </div>

      <label className="post-caption">
        <span className="strip-label">Caption</span>
        <textarea rows={7} value={caption} onChange={(e) => setCaption(e.target.value)} />
      </label>
      <label className="post-caption">
        <span className="strip-label">Hashtags</span>
        <input className="decision-input" value={tags} onChange={(e) => setTags(e.target.value)} />
      </label>

      <div className="model-pick">
        <button className="ref-add" disabled={!dirty || saving || !client} onClick={() => void save()}>
          {saving ? <Loader2 size={13} className="spin" /> : <Save size={13} />} Save changes
        </button>
        <span className="grow" />
        <button
          className="ref-add"
          disabled={!client}
          title="Open Canva in the agent's browser window — sign in there once (a free account is fine)"
          onClick={() => void openCanva(client!).catch((e) => setError(String(e?.message || e)))}
        >
          <Globe size={13} /> Open browser
        </button>
        <button
          className="ref-add"
          disabled={running || dirty || !client}
          title={dirty ? 'Save your changes first' : 'The agent picks a free Canva template and fills it with this plan, in the browser window'}
          onClick={() => onSend(canvaCommand(post))}
        >
          <Palette size={13} /> Design in Canva
        </button>
        <button
          className="prime-btn gen-go"
          disabled={running || dirty || !client}
          title={dirty ? 'Save your changes first' : 'Make the files to upload — no model runs, nothing is paid'}
          onClick={() => onSend(renderCommand(post))}
        >
          <Clapperboard size={14} /> {post.rendered.length ? 'Render again' : 'Render the post'}
        </button>
      </div>

      {post.rendered.length > 0 && (
        <div className="post-out">
          <span className="strip-label">
            Ready to upload, in this order{post.design === 'canva' ? ' — designed in Canva' : ''}
            {post.canva_url && (
              <a className="link-red" href={post.canva_url} target="_blank" rel="noreferrer">
                {' '}
                <ExternalLink size={11} /> open in Canva
              </a>
            )}
          </span>
          <div className="post-files">
            {post.rendered.map((f, i) => (
              <div key={f} className="post-file">
                <ViewableMedia item={fileViews[i]} set={fileViews} />
                <span>{String(i + 1).padStart(2, '0')}</span>
                <a className="post-file-dl" href={fresh(f)} download={f.split('/').pop()} title={`Download ${f.split('/').pop()}`}>
                  <Download size={12} />
                </a>
              </div>
            ))}
          </div>
          <div className="model-pick">
            <a className="ref-add" href={fresh(zip)} download={`${post.slug}.zip`}>
              <Download size={13} /> Download all (zip)
            </a>
            <button
              className="ref-add"
              onClick={() => {
                void navigator.clipboard.writeText(fullCaption)
                setCopied(true)
                window.setTimeout(() => setCopied(false), 1500)
              }}
            >
              <Copy size={13} /> {copied ? 'Copied' : 'Copy caption'}
            </button>
          </div>
          <p className="start-note">AI-generated visuals: switch on Instagram's AI label when you upload. Add the music in Instagram.</p>
        </div>
      )}
    </div>
  )
}
