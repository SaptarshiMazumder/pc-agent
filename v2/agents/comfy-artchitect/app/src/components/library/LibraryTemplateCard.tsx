/* A template in the Library: what it makes, what it needs, and every workflow in it.
 *
 * READ FROM ITS OWN template.json (library-template.ts), never from the catalogue alone, so a
 * saved, an uploaded and — later — a suggested template are drawn by the same code from the
 * same contract. The workflows inside are the Workspace's own card (WorkflowItem), folded under
 * a "Workflows" toggle: the template is used as a whole, so its steps carry no Use buttons.
 */

import { ChevronDown, Download, LayoutTemplate, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import { thumbnailUrl, type Artifact } from '../../agentd/artifacts'
import type { LibraryItem } from '../../agentd/library'
import { downloadTemplate, readTemplate, type TemplateManifest } from '../../agentd/library-template'
import { WorkflowItem } from '../workflows/WorkflowItem'

export function LibraryTemplateCard({
  item,
  client,
  busy,
  onUse,
  onDelete,
}: {
  item: LibraryItem
  client: AgentdClient | undefined
  busy: boolean
  onUse: (item: LibraryItem) => void
  onDelete: () => void
}) {
  const [data, setData] = useState<{ manifest: TemplateManifest; files: Map<string, Artifact> } | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    let alive = true
    if (!client) return
    readTemplate(client, item)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String((e as Error)?.message || e)))
    return () => {
      alive = false
    }
  }, [client, item])

  const download = async (): Promise<void> => {
    if (!client) return
    setDownloading(true)
    setError('')
    try {
      await downloadTemplate(client, item)
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setDownloading(false)
    }
  }

  const m = data?.manifest
  const thumb = m?.thumbnail ? data?.files.get(m.thumbnail) : undefined

  return (
    <section className="wp-item tpl-card">
      <div className="tpl-head">
        {thumb ? (
          <img className="tpl-thumb" src={thumbnailUrl(thumb.path)} alt="" loading="lazy" />
        ) : (
          <span className="tpl-thumb tpl-thumb-blank">
            <LayoutTemplate size={20} strokeWidth={1.6} />
          </span>
        )}
        <div className="tpl-text">
          <span className="wf-card-title">{item.name}</span>
          <span className="wf-card-meta">
            {[
              m ? `${m.steps.length} workflow${m.steps.length === 1 ? '' : 's'}` : '',
              m?.inputs.length ? `inputs: ${m.inputs.map((i) => `@${i.role}`).join(', ')}` : '',
              item.from?.title ? `from: ${item.from.title}` : item.origin === 'uploaded' ? 'uploaded' : '',
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
          {(m?.description || item.note) && <span className="tpl-desc">{m?.description || item.note}</span>}
        </div>
        <button className="wf-card-del" onClick={onDelete} title="Delete this template" aria-label={`Delete ${item.name}`}>
          <Trash2 size={14} strokeWidth={1.8} />
        </button>
      </div>

      {error && <p className="lib-error">{error}</p>}

      <div className="wp-row">
        <button
          type="button"
          className="wp-btn is-primary"
          disabled={busy || !m}
          title={busy ? 'Wait for the current turn to finish' : 'Open a new chat with this template, ready to fill in'}
          onClick={() => onUse(item)}
        >
          Use this template
        </button>
        <button type="button" className="wp-btn" disabled={!m || downloading} onClick={() => void download()}>
          <Download size={14} strokeWidth={1.9} /> {downloading ? 'Zipping…' : 'Download'}
        </button>
        {m && (
          <button type="button" className="wp-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <ChevronDown size={14} strokeWidth={1.9} className={open ? 'tpl-chev is-open' : 'tpl-chev'} /> Workflows
          </button>
        )}
      </div>

      {open && m && data && (
        <div className="tpl-steps">
          {m.steps.map((s, i) => {
            const api = data.files.get(s.api)
            const ui = s.ui ? data.files.get(s.ui) : undefined
            const installers = s.installer.map((p) => data.files.get(p)).filter((a): a is Artifact => !!a)
            return (
              <WorkflowItem
                key={s.role}
                wf={{ name: `${i + 1}. ${s.role}`, api, ui }}
                installers={installers}
                meta={s.slots.length ? `slots: ${s.slots.map((r) => `@${r}`).join(', ')}` : undefined}
              />
            )
          })}
        </div>
      )}
    </section>
  )
}

export default LibraryTemplateCard
