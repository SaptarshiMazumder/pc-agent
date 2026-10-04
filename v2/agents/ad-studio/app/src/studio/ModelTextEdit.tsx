/* New words on a text-ad design whose text the image model drew: the words are part of the
 * picture, so an edit model redraws them — a paid fix, approved by this click like any other.
 * The fields start from the brief's copy; the ones changed become "replace X with Y", and every
 * line is sent as the text the design should carry afterwards, which the check reads back. */

import type { AgentdClient } from '@agentd/client'
import { Loader2, Type, X } from 'lucide-react'
import { useState } from 'react'

import { approveRun, command, type Media, type ModelLists } from '../agentd/campaigns'
import { ModelSelect } from './ModelSelect'

const ORDER = ['headline', 'subline', 'offer', 'cta', 'fine_print']
const ROLE: Record<string, string> = { headline: 'headline', subline: 'subline', offer: 'offer', cta: 'button', fine_print: 'fine print' }
const TEXT_MODEL = 'higgsfield/ideogram_4_5'

export function ModelTextEdit({
  client,
  campaign,
  step,
  path,
  copy,
  media,
  lists,
  busy,
  onSend,
  onClose,
}: {
  client: AgentdClient | null
  campaign: string
  step: string
  path: string
  copy: Record<string, string>
  media: Media
  lists: ModelLists | null
  busy: boolean
  onSend: (text: string) => void
  onClose: () => void
}) {
  const roles = ORDER.filter((r) => copy[r])
  const [words, setWords] = useState<Record<string, string>>(() => Object.fromEntries(roles.map((r) => [r, copy[r]])))
  const [model, setModel] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const fix = lists?.fix
  const chosen = model || (fix?.models.some((m) => m.id === TEXT_MODEL) ? TEXT_MODEL : fix?.default || '')
  const changed = roles.filter((r) => words[r].trim() !== copy[r])

  const send = async () => {
    if (!client || !changed.length) return
    setSending(true)
    setError('')
    try {
      const change =
        'Replace ' +
        changed.map((r) => `the ${ROLE[r]} "${copy[r]}" with "${words[r].trim()}"`).join('; ') +
        '. Keep everything else exactly as it is — the picture, the layout, the colours, the type style and every other word.'
      const approval = await approveRun(client, campaign, step, 'still_fix', { model: chosen, still: path })
      const [provider, ...rest] = chosen.split('/')
      onSend(
        command(`${campaign} · new words on ${path.split('/').pop()} (${chosen})`, 'still_fix', {
          campaign,
          step,
          still: path,
          change,
          provider,
          model: rest.join('/'),
          text: roles.map((r) => words[r].trim()).filter(Boolean),
          approval,
        }),
      )
      onClose()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="act-box">
      <div className="gen-panel-head">
        <span className="strip-label">
          <Type size={12} /> New words — the model drew these, so an edit model redraws them (a paid fix)
        </span>
        <button className="ref-x" onClick={onClose} title="Close">
          <X size={11} />
        </button>
      </div>
      <div className="act-row">
        <img className="act-thumb" src={media(path)} alt="" />
        <div className="te-fields grow">
          {roles.map((r) => (
            <label key={r} className="te-layer">
              <span className="strip-label">{ROLE[r]}</span>
              <input className="decision-input" value={words[r]} disabled={busy} onChange={(e) => setWords((w) => ({ ...w, [r]: e.target.value }))} />
            </label>
          ))}
        </div>
      </div>
      <div className="model-pick">
        {fix && <ModelSelect label="Edit model" value={chosen} models={fix.models} disabled={busy} onChange={setModel} />}
        <button className="prime-btn gen-go" disabled={busy || sending || !client || !changed.length || !chosen} onClick={() => void send()}>
          {sending ? <Loader2 size={14} className="spin" /> : <Type size={14} />} Change the words
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
