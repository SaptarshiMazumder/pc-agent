/* The words of a text-ad design whose text is set in real fonts: each piece of copy a layer — its
 * words, font, size, colour, alignment — over the clean picture. Drag a block to move it. The
 * preview is drawn by the agent exactly as the design will be (the same fonts, the same code), a
 * moment after each change. Save makes a NEW design in the step (the old one stays) and, when
 * the old one was picked, picks the new one. Free and instant: nothing is generated. */

import type { AgentdClient } from '@agentd/client'
import { Loader2, Save, Type, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { pickResult, posterText, type Media, type TextLayer } from '../agentd/campaigns'
import { useApp } from '../state/store'

const FONTS: Record<string, string> = {
  'bold-sans': 'Bold sans',
  condensed: 'Condensed',
  'elegant-serif': 'Elegant serif',
  'body-sans': 'Plain sans',
}
const ROLE: Record<string, string> = { headline: 'Headline', subline: 'Subline', offer: 'Offer', cta: 'Button', fine_print: 'Fine print' }

export function PosterTextEditor({
  client,
  campaign,
  step,
  design,
  picked,
  media,
  onClose,
}: {
  client: AgentdClient | null
  campaign: string
  step: string
  design: string
  picked: boolean
  media: Media
  onClose: () => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [layers, setLayers] = useState<TextLayer[] | null>(null)
  const [shown, setShown] = useState({ path: design, v: 0 })
  const [drawing, setDrawing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const canvas = useRef<HTMLDivElement>(null)
  const timer = useRef<number>()

  useEffect(() => {
    if (!client) return
    posterText(client, { campaign, step, action: 'get', design })
      .then((d) => setLayers(d.text.layers as TextLayer[]))
      .catch((e) => setError(String(e?.message || e)))
  }, [client, campaign, step, design])

  const redraw = (next: TextLayer[]) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      if (!client) return
      setDrawing(true)
      posterText(client, { campaign, step, action: 'preview', design, layers: next })
        .then((d) => {
          setShown((s) => ({ path: String(d.preview), v: s.v + 1 }))
          setError('')
        })
        .catch((e) => setError(String(e?.message || e)))
        .finally(() => setDrawing(false))
    }, 250)
  }
  const change = (i: number, patch: Partial<TextLayer>, draw = true) => {
    setLayers((ls) => {
      if (!ls) return ls
      const next = ls.map((l, n) => (n === i ? { ...l, ...patch } : l))
      if (draw) redraw(next)
      return next
    })
  }

  // Drag a block: its area moves with the pointer, kept inside the frame; drawn on release.
  const drag = (i: number, e: React.PointerEvent) => {
    if (!layers || !canvas.current) return
    e.preventDefault()
    const rect = canvas.current.getBoundingClientRect()
    const [x0, y0, w, h] = layers[i].box
    const start = { x: e.clientX, y: e.clientY }
    const move = (ev: PointerEvent) => {
      const x = Math.min(1 - w, Math.max(0, x0 + (ev.clientX - start.x) / rect.width))
      const y = Math.min(1 - h, Math.max(0, y0 + (ev.clientY - start.y) / rect.height))
      change(i, { box: [x, y, w, h] }, false)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setLayers((ls) => {
        if (ls) redraw(ls)
        return ls
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const save = async () => {
    if (!client || !layers) return
    setSaving(true)
    setError('')
    try {
      const d = await posterText(client, { campaign, step, action: 'save', design, layers })
      const made = String(d.made?.[0]?.path || '')
      if (picked && made) await pickResult(client, campaign, step, made)
      bump()
      onClose()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="act-box text-editor">
      <div className="gen-panel-head">
        <span className="strip-label">
          <Type size={12} /> Edit the words — drag a block to move it {drawing && <Loader2 size={11} className="spin" />}
        </span>
        <button className="ref-x" onClick={onClose} title="Close without saving">
          <X size={11} />
        </button>
      </div>
      <div className="te-body">
        <div className="te-canvas" ref={canvas}>
          <img src={`${media(shown.path)}&v=${shown.v}`} alt="" />
          {(layers || []).map((l, i) => (
            <div
              key={l.role}
              className="te-box"
              title={`${ROLE[l.role] || l.role} — drag to move`}
              style={{ left: `${l.box[0] * 100}%`, top: `${l.box[1] * 100}%`, width: `${l.box[2] * 100}%`, height: `${l.box[3] * 100}%` }}
              onPointerDown={(e) => drag(i, e)}
            />
          ))}
        </div>
        <div className="te-fields">
          {(layers || []).map((l, i) => (
            <div key={l.role} className="te-layer">
              <span className="strip-label">{ROLE[l.role] || l.role}</span>
              <textarea rows={l.role === 'headline' ? 2 : 1} value={l.text} onChange={(e) => change(i, { text: e.target.value })} />
              <div className="te-row">
                <select value={l.font} onChange={(e) => change(i, { font: e.target.value })} title="Font">
                  {Object.entries(FONTS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
                <input
                  type="range"
                  min={0.01}
                  max={0.25}
                  step={0.005}
                  value={l.size}
                  title="Size (the largest it may be; it shrinks to fit its area)"
                  onChange={(e) => change(i, { size: Number(e.target.value) })}
                />
                <input type="color" value={l.color} title="Text colour" onChange={(e) => change(i, { color: e.target.value })} />
                {l.kind === 'pill' && <input type="color" value={l.fill} title="Button colour" onChange={(e) => change(i, { fill: e.target.value })} />}
                <select value={l.align} onChange={(e) => change(i, { align: e.target.value as TextLayer['align'] })} title="Alignment">
                  <option value="left">Left</option>
                  <option value="center">Centre</option>
                  <option value="right">Right</option>
                </select>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="model-pick">
        <span className="strip-label">Free — no model runs. Saved as a new design; this one stays.</span>
        <span className="grow" />
        <button className="prime-btn gen-go" disabled={!layers || saving || !client} onClick={() => void save()}>
          {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
