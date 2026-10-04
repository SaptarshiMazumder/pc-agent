/* Change ONE result: fix an image, edit a clip, or extend it — opened from that result's tile, on
 * exactly that file (or opened by the agent's proposal, pre-filled). The result of the change is
 * added to the step; the original stays. The button first approves exactly this run (its model and
 * target), then sends it to the agent as the exact call. */

import type { AgentdClient } from '@agentd/client'
import { Clapperboard, FastForward, Loader2, Wand2, X } from 'lucide-react'
import { useState } from 'react'

import { approveRun, command, dismissProposal, type Media, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { ModelSelect } from './ModelSelect'

export type ChangeAction = 'fix' | 'edit' | 'extend'

const COPY: Record<ChangeAction, { title: string; placeholder: string; icon: JSX.Element; go: string }> = {
  fix: { title: 'Fix this image', placeholder: 'What to change, only that changes — e.g. her hands rest naturally', icon: <Wand2 size={14} />, go: 'Fix' },
  edit: { title: 'Edit this clip', placeholder: 'What to change in the clip — e.g. make it golden hour', icon: <Clapperboard size={14} />, go: 'Edit' },
  extend: { title: 'Extend this clip', placeholder: 'What happens next — e.g. she turns and walks away', icon: <FastForward size={14} />, go: 'Extend' },
}

export function ResultActionBox({
  client,
  campaign,
  step,
  action,
  path,
  media,
  lists,
  busy,
  onSend,
  onClose,
  proposed,
}: {
  client: AgentdClient | null
  campaign: string
  step: string
  action: ChangeAction
  path: string
  media: Media
  lists: ModelLists | null
  busy: boolean
  onSend: (text: string) => void
  onClose: () => void
  /** The agent's proposal this box was opened for, when it was. */
  proposed?: Record<string, any> | null
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [change, setChange] = useState<string>(proposed?.change || '')
  const [model, setModel] = useState<string>(proposed?.provider && proposed?.model ? `${proposed.provider}/${proposed.model}` : '')
  const [seconds, setSeconds] = useState<number>(Number(proposed?.seconds) || 5)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const list = action === 'fix' ? lists?.fix : action === 'edit' ? lists?.edit : lists?.extend
  const chosen = model || list?.default || ''
  const [provider, modelName] = chosen.includes('/') ? [chosen.split('/')[0], chosen.slice(chosen.indexOf('/') + 1)] : ['', '']
  const name = path.split('/').pop()

  const send = async () => {
    if (!client) return
    setSending(true)
    setError('')
    try {
      const base = { campaign, step, provider, model: modelName, change: change.trim() }
      if (action === 'fix') {
        const approval = await approveRun(client, campaign, step, 'still_fix', { model: chosen, still: path })
        onSend(command(`${campaign} · fix ${name} on ${chosen}: ${change.trim()}`, 'still_fix', { ...base, still: path, approval }))
      } else if (action === 'edit') {
        const approval = await approveRun(client, campaign, step, 'clip_edit', { model: chosen, clip: path, mode: 'edit' })
        onSend(command(`${campaign} · edit ${name} on ${chosen}: ${change.trim()}`, 'clip_edit', { ...base, clip: path, mode: 'edit', approval }))
      } else {
        const approval = await approveRun(client, campaign, step, 'clip_edit', { model: chosen, clip: path, mode: 'extend', seconds })
        onSend(
          command(`${campaign} · extend ${name} by ${seconds} s on ${chosen}: ${change.trim()}`, 'clip_edit', {
            ...base,
            clip: path,
            mode: 'extend',
            seconds,
            approval,
          }),
        )
      }
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
        <span className="strip-label">{proposed ? `Proposed by the agent — ${COPY[action].title.toLowerCase()}` : COPY[action].title}</span>
        <button
          className="ref-x"
          onClick={() => {
            if (proposed && client) void dismissProposal(client, campaign, step).then(bump)
            onClose()
          }}
          title={proposed ? 'Dismiss the proposal' : 'Close'}
        >
          <X size={11} />
        </button>
      </div>
      <div className="act-row">
        {action === 'fix' ? <img className="act-thumb" src={media(path)} alt="" /> : <video className="act-thumb" src={media(path)} muted playsInline preload="metadata" />}
        <input className="decision-input" autoFocus value={change} disabled={busy} placeholder={COPY[action].placeholder} onChange={(e) => setChange(e.target.value)} />
      </div>
      <div className="model-pick">
        {list && (
          <ModelSelect
            label={action === 'fix' ? 'Fix model' : action === 'edit' ? 'Edit model' : 'Extend model'}
            value={chosen}
            models={list.models}
            price={(m) =>
              action === 'edit' ? m.edit?.price || '' : action === 'extend' ? (m.extend ? `continues the clip, ${m.extend.price}` : `new clip from the last frame, ${m.price}`) : m.price
            }
            disabled={busy}
            onChange={setModel}
          />
        )}
        {action === 'extend' && (
          <label className="model-field">
            <span className="strip-label">Add (s)</span>
            <input type="number" min={1} max={30} value={seconds} disabled={busy} onChange={(e) => setSeconds(Math.max(1, Math.min(30, Number(e.target.value) || 5)))} />
          </label>
        )}
        <button className="prime-btn gen-go" disabled={busy || sending || !client || !change.trim() || !chosen} onClick={() => void send()}>
          {sending ? <Loader2 size={14} className="spin" /> : COPY[action].icon} {COPY[action].go}
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
