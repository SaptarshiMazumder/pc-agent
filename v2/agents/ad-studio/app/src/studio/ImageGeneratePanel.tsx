/* Make images for a step — as many times as you like, each run adding to the step.
 *
 * Starts from exactly what the step would send (its prompt, its references) — or from what the
 * agent PROPOSED, when it did: then it is marked as the agent's, for the user to check the model
 * and go, or dismiss. Generate first approves exactly this run (its model and count; a one-time
 * token), then sends it to the agent as the exact call — so nothing is made on a model the user
 * did not choose. */

import type { AgentdClient } from '@agentd/client'
import { ImagePlus, Loader2, RotateCcw, Sparkles, X } from 'lucide-react'
import { useState } from 'react'

import { approveRun, command, dismissProposal, type CampaignStep, type Media, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { ModelSelect } from './ModelSelect'
import { ReferenceTray } from './ReferenceTray'

export function ImageGeneratePanel({
  client,
  campaign,
  step,
  media,
  lists,
  busy,
  like,
  onClearLike,
  onSend,
}: {
  client: AgentdClient | null
  campaign: string
  step: CampaignStep
  media: Media
  lists: ModelLists | null
  busy: boolean
  /** An image to make more like, from a tile's "More like this". */
  like: string
  onClearLike: () => void
  onSend: (text: string) => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const proposed = step.proposal?.tool === 'step_run' ? step.proposal.args || {} : null
  const [prompt, setPrompt] = useState<string>(proposed?.prompt || step.defaults.prompt)
  const [refs, setRefs] = useState<string[]>(proposed?.references || step.defaults.references)
  const [count, setCount] = useState<number>(Number(proposed?.count) || step.count || 3)
  const [model, setModel] = useState<string>(proposed?.model || step.model || lists?.image.default || '')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  // An image step needs the person AND the product: models that take one reference are left out.
  const models = (lists?.image.models || []).filter((m) => m.max_references === null || m.max_references >= 2)
  const chosen = model || lists?.image.default || ''
  const ownPrompt = prompt.trim() !== step.defaults.prompt.trim()
  const ownRefs = refs.join('|') !== step.defaults.references.join('|')
  const likeIt = like || proposed?.like || ''

  const generate = async () => {
    if (!client) return
    setSending(true)
    setError('')
    try {
      const approval = await approveRun(client, campaign, step.id, 'step_run', { model: chosen, count })
      onSend(
        command(
          `${campaign} · ${step.title}: ${count} image${count > 1 ? 's' : ''} on ${chosen}${likeIt ? ' like the one I chose' : ''}${ownPrompt ? ' from my prompt' : ''}`,
          'step_run',
          {
            campaign,
            step: step.id,
            count,
            model: chosen,
            ...(ownPrompt ? { prompt: prompt.trim() } : {}),
            ...(ownRefs ? { references: refs } : {}),
            ...(likeIt ? { like: likeIt } : {}),
            approval,
          },
        ),
      )
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className={`gen-panel${proposed ? ' proposed' : ''}`}>
      <div className="gen-panel-head">
        <span className="strip-label">{proposed ? 'Proposed by the agent — check the model, then Generate' : 'Generate images'}</span>
        {proposed && client && (
          <button
            className="ref-add"
            onClick={() => void dismissProposal(client, campaign, step.id).then(bump)}
            title="Not this one"
          >
            <X size={11} /> dismiss
          </button>
        )}
        {!proposed && ownPrompt && (
          <button className="ref-add" onClick={() => setPrompt(step.defaults.prompt)} title="Back to the step's prompt">
            <RotateCcw size={11} /> step's prompt
          </button>
        )}
      </div>
      <textarea
        className="gen-prompt"
        rows={4}
        value={prompt}
        disabled={busy}
        placeholder="Describe the image — who, what, where, the light"
        onChange={(e) => setPrompt(e.target.value)}
      />
      <ReferenceTray value={refs} defaults={step.defaults.references} media={media} onChange={setRefs} />
      {likeIt && (
        <div className="like-chip">
          <img src={media(likeIt)} alt="" /> More like this one
          <button className="ref-x" onClick={onClearLike} title="Not like this one">
            <X size={10} />
          </button>
        </div>
      )}
      <div className="model-pick">
        {lists && <ModelSelect label="Model" value={chosen} models={models} disabled={busy} onChange={setModel} />}
        <label className="model-field">
          <span className="strip-label">Images</span>
          <input type="number" min={1} max={4} value={count} disabled={busy} onChange={(e) => setCount(Math.max(1, Math.min(4, Number(e.target.value) || 1)))} />
        </label>
        <button
          className="prime-btn gen-go"
          disabled={busy || sending || !client || !prompt.trim() || !chosen}
          onClick={() => void generate()}
          title={busy ? 'Wait for the agent to finish its turn' : `Approve ${chosen} and generate`}
        >
          {sending ? <Loader2 size={14} className="spin" /> : likeIt ? <ImagePlus size={14} /> : <Sparkles size={14} />} Generate
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
