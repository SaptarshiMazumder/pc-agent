/* The design references on the Posts page: pictures of designs worth following — Canva previews the
 * agent collected, your own screenshots — each with what it suits and what a designer read off it.
 * Designs follow their structure with the post's own pictures and words. Add screenshots (the
 * agent's model reads each one — a few seconds), open one full screen, drop one. */

import type { AgentdClient } from '@agentd/client'
import { ExternalLink, Loader2, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { addReference, deleteReference, listReferences, type DesignReference } from '../agentd/posts'
import { useApp } from '../state/store'
import { ViewableMedia, viewerItem } from '../studio/ViewableMedia'

const SPEC_LABELS: [keyof DesignReference['spec'], string][] = [
  ['layout', 'Layout'],
  ['photos', 'Photos'],
  ['type', 'Type'],
  ['palette', 'Palette'],
  ['decoration', 'Details'],
  ['text_slots', 'Words'],
  ['mood', 'Mood'],
]

export function ReferenceLibrary({ client }: { client: AgentdClient | null }) {
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const input = useRef<HTMLInputElement>(null)
  const [data, setData] = useState<{ references: DesignReference[]; media: Media } | null>(null)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState('')
  const [open, setOpen] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    if (!client) return
    listReferences(client)
      .then((d) => setData(d))
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  const add = async (files: File[]) => {
    if (!client || !files.length) return
    setError('')
    try {
      for (const [i, file] of files.entries()) {
        setBusy(`Reading ${i + 1} of ${files.length}…`)
        await addReference(client, file, '', notes.trim())
      }
      setNotes('')
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy('')
      bump()
      if (input.current) input.current.value = ''
    }
  }
  const drop = async (r: DesignReference) => {
    if (!client || !window.confirm(`Delete the reference “${r.name}”?`)) return
    try {
      await deleteReference(client, r.slug)
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }

  const refs = data?.references || []
  const views = refs.map((r) => viewerItem(data!.media(r.image), r.image, `${r.name}${r.suits.length ? ` — ${r.suits.join(', ')}` : ''}`))
  return (
    <section className="template-gallery">
      <p className="page-note">
        Designs worth following. The agent collects them from Canva (ask it: “collect design references for festive jewellery posts”), or add screenshots of
        designs you like — one design per picture. Posts follow their structure with your own pictures and words, never theirs.
      </p>
      <div className="collection-import">
        <input className="decision-input" value={notes} disabled={!!busy} placeholder="What you like about them (optional) — e.g. the circle crops, the script accent" onChange={(e) => setNotes(e.target.value)} />
        <input ref={input} type="file" multiple accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => void add(Array.from(e.target.files || []))} />
        <button className="ref-add" disabled={!!busy || !client} onClick={() => input.current?.click()}>
          {busy ? <Loader2 size={13} className="spin" /> : <Upload size={13} />} {busy || 'Add screenshots'}
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
      <div className="template-grid">
        {refs.map((r, i) => (
          <figure key={r.slug} className={`template-card reference-card${open === r.slug ? ' open' : ''}`}>
            <ViewableMedia item={views[i]} set={views} />
            <figcaption>
              <span className="camp-name">{r.name}</span>
              <span className="collection-meta">{r.suits.join(' · ') || '—'}</span>
              <span className="reference-acts">
                <button className="ref-x" onClick={() => setOpen(open === r.slug ? '' : r.slug)} title="What a designer reads off it">
                  {open === r.slug ? 'Less' : 'Spec'}
                </button>
                {/^https?:/.test(r.source) && (
                  <a className="ref-x" href={r.source} target="_blank" rel="noreferrer" title="Where it was found">
                    <ExternalLink size={11} />
                  </a>
                )}
                <button className="ref-x" onClick={() => void drop(r)} title="Delete it">
                  <Trash2 size={11} />
                </button>
              </span>
              {open === r.slug && (
                <dl className="reference-spec">
                  {SPEC_LABELS.filter(([k]) => r.spec[k]).map(([k, label]) => (
                    <div key={k}>
                      <dt>{label}</dt>
                      <dd>{r.spec[k]}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </figcaption>
          </figure>
        ))}
        {data && !refs.length && <div className="ref-none">No references yet.</div>}
      </div>
    </section>
  )
}
