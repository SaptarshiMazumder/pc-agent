/* "+ Product still": a step of the user's own with no person in it — the product in a gift box, on
 * a glass table, as a flat lay — added to any campaign in one line. Added directly (free, instant);
 * the new step opens, and its Generate panel makes the images, as many times as the user likes.
 * The product's photos and must-keep details and the natural-light photo style ride along with
 * every image, so the line only needs to say the scene. */

import type { AgentdClient } from '@agentd/client'
import { Grid3x3, Loader2, PackagePlus, X } from 'lucide-react'
import { useState } from 'react'

import { addStep } from '../agentd/campaigns'
import { useApp } from '../state/store'

const IDEAS = [
  'In an open gift box with a light silver satin lining, seen from directly above, on a glass table',
  'A clean packshot on a plain, light surface',
  'On a marble counter by a window',
  'A flat lay on linen, from above',
]

export function AddProductStill({
  client,
  campaign,
  onAdded,
}: {
  client: AgentdClient | null
  campaign: string
  onAdded: (step: string) => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [open, setOpen] = useState(false)
  const [scene, setScene] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const add = async () => {
    if (!client || !scene.trim()) return
    setBusy(true)
    setError('')
    try {
      const id = await addStep(client, campaign, {
        title: 'Product still',
        action: 'images',
        prompt: `The product alone, with no person: ${scene.trim()}.`,
        cast: false,
        shows_product: true,
      })
      bump()
      onAdded(id)
      setScene('')
      setOpen(false)
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const addSheet = async () => {
    if (!client) return
    setBusy(true)
    setError('')
    try {
      const id = await addStep(client, campaign, {
        title: 'Product sheet',
        action: 'product_sheet',
        prompt: '',
        cast: false,
        shows_product: true,
      })
      bump()
      onAdded(id)
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  if (!open)
    return (
      <div className="add-step-row">
        <button className="ref-add add-step" onClick={() => setOpen(true)} disabled={!client} title="A still of the product alone — no person">
          <PackagePlus size={13} /> Product still
        </button>
        <button
          className="ref-add add-step"
          onClick={() => void addSheet()}
          disabled={!client || busy}
          title="Six views of the product from its photos — every later image and clip uses them as references"
        >
          {busy ? <Loader2 size={13} className="spin" /> : <Grid3x3 size={13} />} Product sheet
        </button>
        {error && <div className="studio-error">{error}</div>}
      </div>
    )
  return (
    <div className="act-box">
      <div className="gen-panel-head">
        <span className="strip-label">A product still — the product alone, no person</span>
        <button className="ref-x" onClick={() => setOpen(false)} title="Close">
          <X size={11} />
        </button>
      </div>
      <div className="act-row">
        <input
          className="decision-input"
          autoFocus
          value={scene}
          disabled={busy}
          placeholder="Where and how — e.g. in a gift box, top down, on a glass table"
          onChange={(e) => setScene(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void add()
          }}
        />
        <button className="prime-btn gen-go" disabled={busy || !scene.trim()} onClick={() => void add()}>
          {busy ? <Loader2 size={14} className="spin" /> : <PackagePlus size={14} />} Add step
        </button>
      </div>
      <div className="scene-chips">
        {IDEAS.map((i) => (
          <button key={i} className="filter-chip scene-chip" onClick={() => setScene(i)} disabled={busy}>
            {i}
          </button>
        ))}
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
