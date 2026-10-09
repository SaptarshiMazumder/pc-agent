/* One stage of the campaign, whatever its state: what it made on the left (StageResults), the form
 * that makes more on the right (StageInspector).
 *
 * This file is the stage's controller: the state both halves share — the result being picked, the
 * action box open on one result, the image "more like this" starts from — and the free, direct
 * changes (pick, skip) with their errors.
 *
 * NOTHING HERE IS LOCKED: a stage done long ago works exactly like the current one. Picking and
 * skipping are instant (direct, free); anything that generates is sent to the agent as the exact
 * call. Moving on to the next stage is the carry-forward bar's (CarryForwardBar). */

import type { AgentdClient } from '@agentd/client'
import { RotateCcw, SkipForward } from 'lucide-react'
import { useEffect, useState } from 'react'

import { pickResult, updateStep, type CampaignDetail, type CampaignStep, type Media, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { StageInspector } from './StageInspector'
import { StageResults, type ActionBox } from './StageResults'
import type { TileAction } from './ResultGrid'

export function StepPanel({
  client,
  campaign,
  step,
  media,
  lists,
  busy,
  onSend,
}: {
  client: AgentdClient | null
  campaign: CampaignDetail
  step: CampaignStep
  media: Media
  lists: ModelLists | null
  /** The agent is mid-turn: a message now would land in the middle of it. */
  busy: boolean
  onSend: (text: string) => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [picking, setPicking] = useState('')
  const [error, setError] = useState('')
  // An agent's proposal to fix or edit one result opens its box, pre-filled.
  const proposal = step.proposal?.tool ? step.proposal : null
  const proposedBox: ActionBox =
    proposal?.tool === 'still_fix'
      ? { action: 'fix', path: String(proposal.args?.still || '') }
      : proposal?.tool === 'clip_edit'
        ? { action: proposal.args?.mode === 'extend' ? 'extend' : 'edit', path: String(proposal.args?.clip || '') }
        : null
  const [box, setBox] = useState<ActionBox>(proposedBox)
  const proposalKey = JSON.stringify(step.proposal || {})
  useEffect(() => {
    if (proposedBox) setBox(proposedBox)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposalKey])
  const [like, setLike] = useState('')
  const id = campaign.campaign_id
  const index = campaign.steps.findIndex((s) => s.id === step.id)
  const source = step.source ? campaign.steps.find((s) => s.id === step.source) : undefined
  // A text ad's design step: it has words — set in real fonts (editable layers) or drawn by the model.
  const copy = campaign.brief?.copy || {}
  const poster = step.action === 'images' && !!step.scene && Object.values(copy).some(Boolean)

  const direct = (what: () => Promise<void>) => {
    if (!client) return
    setError('')
    void what()
      .then(bump)
      .catch((e) => setError(String((e as Error)?.message || e)))
  }
  const pick = (path: string) => {
    if (!client) return
    setPicking(path)
    setError('')
    pickResult(client, id, step.id, path)
      .then(bump)
      .catch((e) => setError(String((e as Error)?.message || e)))
      .finally(() => setPicking(''))
  }
  const onAction = (action: TileAction, path: string) => {
    if (action === 'like') {
      setLike(path)
      setBox(null)
      return
    }
    setBox(box?.action === action && box.path === path ? null : { action, path })
  }

  return (
    <div className="stage">
      <div className="stage-head">
        <div className="stage-head-title">
          <h3>
            Stage {index + 1} · {step.title}
          </h3>
          <span className="gate-hint">
            {step.status === 'done'
              ? `done · ${step.results.length} made${step.pick ? ' · one picked' : ''} — run it again any time`
              : step.status === 'skipped'
                ? 'skipped'
                : step.action === 'brief'
                  ? 'not written yet'
                  : 'not made yet'}
          </span>
        </div>
        {step.action !== 'brief' && (
          <button
            className="stage-skip"
            disabled={!client}
            onClick={() => direct(() => updateStep(client!, id, step.id, { status: step.status === 'skipped' ? 'todo' : 'skipped' }))}
            title={step.status === 'skipped' ? 'Open this stage again' : 'Skip this stage'}
          >
            {step.status === 'skipped' ? <RotateCcw size={13} /> : <SkipForward size={13} />} {step.status === 'skipped' ? 'Open again' : 'Skip stage'}
          </button>
        )}
      </div>
      {error && <div className="studio-error">{error}</div>}

      <div className="stage-body">
        <StageResults
          client={client}
          campaign={campaign}
          step={step}
          source={source}
          media={media}
          lists={lists}
          busy={busy}
          poster={poster}
          copy={copy}
          picking={picking}
          box={box}
          proposedBox={proposedBox}
          onPick={pick}
          onAction={onAction}
          onCloseBox={() => setBox(null)}
          onSend={onSend}
          direct={direct}
        />
        <StageInspector
          client={client}
          campaign={id}
          step={step}
          source={source}
          media={media}
          lists={lists}
          busy={busy}
          like={like}
          onClearLike={() => setLike('')}
          onSend={onSend}
          direct={direct}
        />
      </div>
    </div>
  )
}
