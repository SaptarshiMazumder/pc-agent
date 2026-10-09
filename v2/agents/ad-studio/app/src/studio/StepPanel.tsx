/* One step of the campaign, whatever its state: everything it made, and everything it can do.
 *
 *   brief   the look and the scene; rewrite it with a change
 *   sheet   the shoot sheets made; pick one; make another with a change
 *   images  every image made or uploaded; pick, fix, "more like this"; Generate (prompt,
 *           references, model…); Upload your own images into it
 *   video   every clip made; pick, edit, extend; Generate from any image
 *
 * NOTHING HERE IS LOCKED: a step done long ago works exactly like the current one. Picking and
 * skipping are instant (direct, free); anything that generates is sent to the agent as the exact
 * call. "Next" just opens the next step — following the recipe is opening each step and
 * generating. */

import type { AgentdClient } from '@agentd/client'
import { AlertTriangle, ArrowRight, RotateCcw, SkipForward, Type } from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  approveRun,
  command,
  pickResult,
  posterText,
  updateStep,
  type CampaignDetail,
  type CampaignStep,
  type Media,
  type ModelLists,
} from '../agentd/campaigns'
import { useApp } from '../state/store'
import { BriefView } from './BriefView'
import { ViewableMedia, viewerItem } from './ViewableMedia'
import { ImageGeneratePanel } from './ImageGeneratePanel'
import { ModelSelect } from './ModelSelect'
import { ModelTextEdit } from './ModelTextEdit'
import { PosterTextEditor } from './PosterTextEditor'
import { ResultActionBox, type ChangeAction } from './ResultActionBox'
import { ResultGrid, type TileAction } from './ResultGrid'
import { UploadImages } from './UploadImages'
import { VideoGeneratePanel } from './VideoGeneratePanel'

export function StepPanel({
  client,
  campaign,
  step,
  media,
  lists,
  busy,
  onSend,
  onShow,
}: {
  client: AgentdClient | null
  campaign: CampaignDetail
  step: CampaignStep
  media: Media
  lists: ModelLists | null
  /** The agent is mid-turn: a message now would land in the middle of it. */
  busy: boolean
  onSend: (text: string) => void
  onShow: (step: string) => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [picking, setPicking] = useState('')
  const [error, setError] = useState('')
  // An agent's proposal to fix or edit one result opens its box, pre-filled.
  const proposal = step.proposal?.tool ? step.proposal : null
  const proposedBox: { action: TileAction; path: string } | null =
    proposal?.tool === 'still_fix'
      ? { action: 'fix', path: String(proposal.args?.still || '') }
      : proposal?.tool === 'clip_edit'
        ? { action: proposal.args?.mode === 'extend' ? 'extend' : 'edit', path: String(proposal.args?.clip || '') }
        : null
  const [box, setBox] = useState<{ action: TileAction; path: string } | null>(proposedBox)
  const proposalKey = JSON.stringify(step.proposal || {})
  useEffect(() => {
    if (proposedBox) setBox(proposedBox)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposalKey])
  const [like, setLike] = useState('')
  // A shoot sheet the agent proposed: its change and model, pre-filled.
  const isSheet = step.action === 'sheet' || step.action === 'product_sheet'
  const sheetProposal = isSheet && proposal?.tool === 'step_run' ? proposal.args || {} : null
  const [change, setChange] = useState<string>(sheetProposal?.change || '')
  const [sheetModel, setSheetModel] = useState<string>(sheetProposal?.model || '')
  const id = campaign.campaign_id
  const index = campaign.steps.findIndex((s) => s.id === step.id)
  const next = campaign.steps.slice(index + 1).find((s) => s.status !== 'skipped')
  const source = step.source ? campaign.steps.find((s) => s.id === step.source) : undefined
  // A text ad's design step: it has words — set in real fonts (editable layers) or drawn by the model.
  const copy = campaign.brief?.copy || {}
  const poster = step.action === 'images' && !!step.scene && Object.values(copy).some(Boolean)
  const boxResult = box ? step.results.find((r) => r.path === box.path) : undefined

  const direct = async (what: () => Promise<void>) => {
    if (!client) return
    setError('')
    try {
      await what()
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }
  const pick = (path: string) => {
    setPicking(path)
    void direct(() => pickResult(client!, id, step.id, path)).finally(() => setPicking(''))
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
    <div className="step-panel">
      <div className="step-panel-head">
        <span className="gate-badge">{step.title}</span>
        <span className="gate-hint">
          {step.status === 'done'
            ? `done · ${step.results.length} made${step.pick ? ' · one picked' : ''} — run it again any time`
            : step.status === 'skipped'
              ? 'skipped'
              : step.action === 'brief'
                ? 'not written yet'
                : 'not made yet'}
        </span>
        <span className="grow" />
        {step.action !== 'brief' && (
          <button
            className="link-red"
            disabled={!client}
            onClick={() => void direct(() => updateStep(client!, id, step.id, { status: step.status === 'skipped' ? 'todo' : 'skipped' }))}
            title={step.status === 'skipped' ? 'Open this step again' : 'Skip this step'}
          >
            {step.status === 'skipped' ? <RotateCcw size={13} /> : <SkipForward size={13} />} {step.status === 'skipped' ? 'Open again' : 'Skip'}
          </button>
        )}
        {next && (
          <button className="link-red" onClick={() => onShow(next.id)} title={`Open ${next.title}`}>
            Next: {next.title} <ArrowRight size={13} />
          </button>
        )}
      </div>
      {error && <div className="studio-error">{error}</div>}

      {step.action === 'brief' && (
        <>
          {campaign.brief ? <BriefView brief={campaign.brief} direction={campaign.direction} /> : <div className="ref-none">No brief yet.</div>}
          <div className="gen-panel">
            <span className="strip-label">Rewrite the brief</span>
            <div className="act-row">
              <input className="decision-input" value={change} disabled={busy} placeholder="What to change — e.g. a temple courtyard, soft daylight, she sits on the steps" onChange={(e) => setChange(e.target.value)} />
              <button
                className="prime-btn gen-go"
                disabled={busy || !change.trim()}
                onClick={() => {
                  onSend(command(`${id} · rewrite the brief: ${change.trim()}`, 'step_run', { campaign: id, step: step.id, change: change.trim() }))
                  setChange('')
                }}
              >
                <RotateCcw size={14} /> Rewrite
              </button>
            </div>
          </div>
        </>
      )}

      {isSheet && (
        <>
          <UploadImages client={client} campaign={id} step={step.id} label={step.action === 'product_sheet' ? 'Upload your own sheet' : 'Upload a sheet'}>
            <ResultGrid campaign={id} step={step} media={media} picking={picking} onPick={pick} actions={[]} active={null} onAction={onAction} />
          </UploadImages>
          {step.action === 'product_sheet' && (step.defaults.panels || []).length > 0 && (
            <div className="another">
              <span className="strip-label">Sent to the models as six separate references</span>
              <div className="another-row">
                {(step.defaults.panels || []).map((p, i, all) => (
                  <div key={p} className="another-still panel-view" title={p}>
                    <ViewableMedia item={viewerItem(media(p), p, `Panel ${i + 1}`)} set={all.map((x, k) => viewerItem(media(x), x, `Panel ${k + 1}`))} />
                  </div>
                ))}
              </div>
            </div>
          )}
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
            <div className="act-row">
              <input
                className="decision-input"
                value={change}
                disabled={busy}
                placeholder={
                  step.action === 'product_sheet'
                    ? "Optional — what to change (the back's colour, the label's wording…)"
                    : "Optional — what to change (her hair, the outfit's fit…)"
                }
                onChange={(e) => setChange(e.target.value)}
              />
            </div>
            <div className="model-pick">
              {lists && (
                <ModelSelect
                  label="Model"
                  value={sheetModel || step.model || lists.image.default}
                  models={
                    step.action === 'product_sheet'
                      ? lists.image.models.filter((m) => m.max_references === null || m.max_references >= 2)
                      : lists.image.models
                  }
                  disabled={busy}
                  onChange={setSheetModel}
                />
              )}
              <button
                className="prime-btn gen-go"
                disabled={busy || !client}
                onClick={() =>
                  void direct(async () => {
                    const model = sheetModel || step.model || lists?.image.default || ''
                    const approval = await approveRun(client!, id, step.id, 'step_run', { model })
                    onSend(
                      command(`${id} · ${step.title} on ${model}${change.trim() ? `: ${change.trim()}` : ''}`, 'step_run', {
                        campaign: id,
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
        </>
      )}

      {(step.action === 'images' || step.action === 'video') && (
        <>
          {step.stale && (
            <div className="stale-note">
              <AlertTriangle size={14} /> The picked clip was made from an image {source?.title || 'its source step'} no longer picks. Generate a new one
              below (it starts from the new pick), or keep this one.
            </div>
          )}
          {step.action === 'images' ? (
            <UploadImages client={client} campaign={id} step={step.id}>
              <ResultGrid
                campaign={id}
                step={step}
                media={media}
                picking={picking}
                onPick={pick}
                actions={poster ? ['text', 'fix', 'like'] : ['fix', 'like']}
                active={box}
                onAction={onAction}
              />
            </UploadImages>
          ) : (
            <ResultGrid campaign={id} step={step} media={media} picking={picking} onPick={pick} actions={['edit', 'extend']} active={box} onAction={onAction} />
          )}
          {box?.action === 'text' && boxResult?.editable && (
            <PosterTextEditor
              key={box.path}
              client={client}
              campaign={id}
              step={step.id}
              design={box.path}
              picked={step.pick === box.path}
              media={media}
              onClose={() => setBox(null)}
            />
          )}
          {box?.action === 'text' && boxResult && !boxResult.editable && (
            <ModelTextEdit
              key={box.path}
              client={client}
              campaign={id}
              step={step.id}
              path={box.path}
              copy={copy}
              media={media}
              lists={lists}
              busy={busy}
              onSend={onSend}
              onClose={() => setBox(null)}
            />
          )}
          {poster && (
            <div className="text-mode">
              <span className="strip-label">
                <Type size={12} /> The words
              </span>
              <div className="seg">
                <button
                  className={step.text !== 'overlay' ? 'on' : ''}
                  disabled={!client}
                  title="The image model draws the words into the picture; each design's text is read back by the check"
                  onClick={() => void direct(() => updateStep(client!, id, step.id, { text: '' }))}
                >
                  Drawn by the model
                </button>
                <button
                  className={step.text === 'overlay' ? 'on' : ''}
                  disabled={!client}
                  title="The model makes the picture with empty space; the words are set on it in real fonts — always spelled right, editable for free"
                  onClick={() => void direct(() => updateStep(client!, id, step.id, { text: 'overlay' }))}
                >
                  Set in real fonts (editable)
                </button>
              </div>
              {step.results.some((r) => r.editable) && (
                <button
                  className="link-red"
                  disabled={!client}
                  title="Put the brief's current words on every real-font design (free)"
                  onClick={() => void direct(() => posterText(client!, { campaign: id, step: step.id, action: 'retext_all' }).then(() => undefined))}
                >
                  Put the brief's words on all
                </button>
              )}
            </div>
          )}
          {box && box.action !== 'like' && box.action !== 'text' && (
            <ResultActionBox
              key={`${box.action}:${box.path}:${JSON.stringify(proposal || {})}`}
              client={client}
              proposed={proposedBox && proposedBox.path === box.path && proposedBox.action === box.action ? proposal?.args : null}
              campaign={id}
              step={step.id}
              action={box.action as ChangeAction}
              path={box.path}
              media={media}
              lists={lists}
              busy={busy}
              onSend={onSend}
              onClose={() => setBox(null)}
            />
          )}
          {step.action === 'images' ? (
            <ImageGeneratePanel
              key={`${step.id}:${step.defaults.prompt}:${step.defaults.references.join('|')}:${JSON.stringify(step.proposal || {})}`}
              client={client}
              campaign={id}
              step={step}
              media={media}
              lists={lists}
              busy={busy}
              like={like}
              onClearLike={() => setLike('')}
              onSend={(t) => {
                onSend(t)
                setLike('')
              }}
            />
          ) : (
            <VideoGeneratePanel
              key={`${step.id}:${source?.pick || ''}:${step.defaults.prompt}:${JSON.stringify(step.proposal || {})}`}
              client={client}
              campaign={id}
              step={step}
              source={source}
              media={media}
              lists={lists}
              busy={busy}
              onSend={onSend}
            />
          )}
        </>
      )}
    </div>
  )
}
