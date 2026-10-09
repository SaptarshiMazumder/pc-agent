/* The brand every post carries: the account's name and handle, the line posts sign off with, how it
 * talks, its fonts and colours. Saved directly — free and instant. */

import type { AgentdClient } from '@agentd/client'
import { Loader2, Save } from 'lucide-react'
import { useEffect, useState } from 'react'

import { getBrand, HEADING_FONTS, LABEL_FONTS, setBrand, type Brand } from '../agentd/posts'

export function BrandForm({ client }: { client: AgentdClient | null }) {
  const [brand, setLocal] = useState<Brand | null>(null)
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!client) return
    getBrand(client)
      .then((b) => {
        setLocal(b)
        setSaved(JSON.stringify(b))
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client])
  if (!brand) return error ? <div className="studio-error">{error}</div> : null
  const field = (k: Exclude<keyof Brand, 'design_notes'>, label: string, placeholder = '') => (
    <label className="brand-field">
      <span className="strip-label">{label}</span>
      <input className="decision-input" value={brand[k]} placeholder={placeholder} onChange={(e) => setLocal({ ...brand, [k]: e.target.value })} />
    </label>
  )
  const save = async () => {
    if (!client) return
    setBusy(true)
    setError('')
    try {
      const b = await setBrand(client, { ...brand, design_notes: brand.design_notes.map((n) => n.trim()).filter(Boolean) })
      setLocal(b)
      setSaved(JSON.stringify(b))
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="brand-form">
      <span className="eyebrow-red">Brand</span>
      <div className="brand-grid">
        {field('name', 'Account name', 'e.g. The Finds Edit')}
        {field('handle', 'Instagram handle', 'without @')}
        {field('tagline', 'Sign-off line (last slide)', 'e.g. We find beautiful things for women.')}
        {field('voice', 'How it talks', 'e.g. a discovery page with taste — warm, a little fantasy, never salesy')}
        {field('caption_disclosure', 'Caption disclosure (ends every caption)', 'e.g. Independently curated. Not sponsored.')}
        <label className="brand-field">
          <span className="strip-label">Heading font</span>
          <select value={brand.heading_font} onChange={(e) => setLocal({ ...brand, heading_font: e.target.value })}>
            {HEADING_FONTS.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label className="brand-field">
          <span className="strip-label">Label font</span>
          <select value={brand.label_font} onChange={(e) => setLocal({ ...brand, label_font: e.target.value })}>
            {LABEL_FONTS.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label className="brand-field">
          <span className="strip-label">Text colour</span>
          <input type="color" value={brand.text_color} onChange={(e) => setLocal({ ...brand, text_color: e.target.value })} />
        </label>
        <label className="brand-field">
          <span className="strip-label">Accent colour</span>
          <input type="color" value={brand.accent_color} onChange={(e) => setLocal({ ...brand, accent_color: e.target.value })} />
        </label>
      </div>
      <label className="brand-field brand-notes">
        <span className="strip-label">Design notes — rules every design follows (one per line; the agent adds the ones you teach it)</span>
        <textarea
          className="decision-input"
          rows={5}
          value={brand.design_notes.join('\n')}
          placeholder={'e.g. Every picture full-bleed — text and shapes over it\nNever let a shape touch her face or hair'}
          onChange={(e) => setLocal({ ...brand, design_notes: e.target.value.split('\n') })}
        />
      </label>
      <div className="page-actions">
        <button className="prime-btn" disabled={busy || JSON.stringify(brand) === saved || !client} onClick={() => void save()}>
          {busy ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save brand
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </section>
  )
}
