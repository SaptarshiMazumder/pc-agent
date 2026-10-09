/* The right half of a stage: the form that makes more for it — as many times as you like, each run
 * adding to the stage.
 *
 *   brief   rewrite it with a change
 *   sheet   make another (a change, the model) — or approve the one the agent proposed
 *   images  ImageGeneratePanel (prompt, references, model, count)
 *   video   VideoGeneratePanel (first frame, motion, references, model, length, resolution)
 *
 * Anything that generates is approved here (its model; a one-time token) and sent to the agent as
 * the exact call, so the thread records what was asked for. */

import type { AgentdClient } from '@agentd/client'
import { RotateCcw } from 'lucide-react'
import { useState } from 'react'

import { approveRun, command, type CampaignStep, type Media, type ModelLists } from '../agentd/campaigns'
import { ImageGeneratePanel } from './ImageGeneratePanel'
import { ModelSelect } from './ModelSelect'
import { VideoGeneratePanel } from './VideoGeneratePanel'

export function StageInspector({
  client,
  campaign,
  step,
  source,
  media,
  lists,
  busy,
  like,
  onClearLike,
  onSend,
  direct,
}: {
  client: AgentdClient | null
  campaign: string
  step: CampaignStep
  source: CampaignStep | undefined
  media: Media
  lists: ModelLists | null
  busy: boolean
  /** An image to make more like ("More like this" on a result). */
  like: string
  onClearLike: () => void
  onSend: (text: string) => void
  direct: (what: () => Promise<void>) => void
}) {
  const proposal = step.proposal?.tool ? step.proposal : null
  const isSheet = step.action === 'sheet' || step.action === 'product_sheet'
  // A shoot sheet the agent proposed: its change and model, pre-filled.
  const sheetProposal = isSheet && proposal?.tool === 'step_run' ? proposal.args || {} : null
  const [change, setChange] = useState<string>(sheetProposal?.change || '')
  const [sheetModel, setSheetModel] = useState<string>(sheetProposal?.model || '')

  if (step.action === 'brief')
    return (
      <aside className="stage-inspector">
        <div className="gen-panel">
          <span className="strip-label">Rewrite the brief</span>
          <textarea
            className="gen-prompt"
            rows={3}
            value={change}
            disabled={busy}
            placeholder="What to change — e.g. a temple courtyard, soft daylight, she sits on the steps"
            onChange={(e) => setChange(e.target.value)}
          />
          <button
            className="prime-btn gen-go"
            disabled={busy || !change.trim()}
            onClick={() => {
              onSend(command(`${campaign} · rewrite the brief: ${change.trim()}`, 'step_run', { campaign, step: step.id, change: change.trim() }))
              setChange('')
            }}
          >
            <RotateCcw size={14} /> Rewrite
          </button>
        </div>
      </aside>
    )

  if (isSheet)
    return (
      <aside className="stage-inspector">
        <div className={`gen-panel${sheetProposal ? ' proposed' : ''}`}>
          <span className="strip-label">
            {sheetProposal
              ? 'Proposed by the agent — check the model, then Generate'
              : step.action === 'product_sheet'
                ? step.results.length
                  ? 'Another product sheet'
                  : 'Make the product sheet — six views of the product from its photos'
                : step.results.length
                  ? 'Another shoot sheet'
                  : 'Make the shoot sheet'}
          </span>
          <input
            className="decision-input"
            value={change}
            disabled={busy}
            placeholder={
              step.action === 'product_sheet' ? "Optional — what to change (the back's colour, the label's wording…)" : "Optional — what to change (her hair, the outfit's fit…)"
            }
            onChange={(e) => setChange(e.target.value)}
          />
          <div className="model-pick">
            {lists && (
              <ModelSelect
                label="Model"
                value={sheetModel || step.model || lists.image.default}
                models={
                  step.action === 'product_sheet' ? lists.image.models.filter((m) => m.max_references === null || m.max_references >= 2) : lists.image.models
                }
                disabled={busy}
                onChange={setSheetModel}
              />
            )}
            <button
              className="prime-btn gen-go"
              disabled={busy || !client}
              onClick={() =>
                direct(async () => {
                  const model = sheetModel || step.model || lists?.image.default || ''
                  const approval = await approveRun(client!, campaign, step.id, 'step_run', { model })
                  onSend(
                    command(`${campaign} · ${step.title} on ${model}${change.trim() ? `: ${change.trim()}` : ''}`, 'step_run', {
                      campaign,
                      step: step.id,
                      change: change.trim(),
                      model,
                      approval,
                    }),
                  )
                  setChange('')
                })
              }
            >
              Generate
            </button>
          </div>
        </div>
      </aside>
    )

  return (
    <aside className="stage-inspector">
      {step.action === 'images' ? (
        <ImageGeneratePanel
          key={`${step.id}:${step.defaults.prompt}:${step.defaults.references.join('|')}:${JSON.stringify(step.proposal || {})}`}
          client={client}
          campaign={campaign}
          step={step}
          media={media}
          lists={lists}
          busy={busy}
          like={like}
          onClearLike={onClearLike}
          onSend={(t) => {
            onSend(t)
            onClearLike()
          }}
        />
      ) : (
        <VideoGeneratePanel
          key={`${step.id}:${source?.pick || ''}:${step.defaults.prompt}:${JSON.stringify(step.proposal || {})}`}
          client={client}
          campaign={campaign}
          step={step}
          source={source}
          media={media}
          lists={lists}
          busy={busy}
          onSend={onSend}
        />
      )}
    </aside>
  )
}
