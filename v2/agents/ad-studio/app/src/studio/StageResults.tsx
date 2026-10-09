/* The left half of a stage: everything it made, and what can be done to each result.
 *
 *   brief   the look and the scene
 *   sheet   the shoot sheets made (or uploaded); a product sheet's six panels
 *   images  every image made or uploaded; pick, fix, "more like this", edit a design's words
 *   video   every clip made; pick, edit, extend
 *
 * Picking is instant and free (onPick); an action on one result opens its box under the grid. The
 * forms that make MORE are the stage's other half (StageInspector). */

import type { AgentdClient } from '@agentd/client'
import { AlertTriangle, Type } from 'lucide-react'

import { posterText, updateStep, type CampaignDetail, type CampaignStep, type Media, type ModelLists } from '../agentd/campaigns'
import { BriefView } from './BriefView'
import { ModelTextEdit } from './ModelTextEdit'
import { PosterTextEditor } from './PosterTextEditor'
import { ResultActionBox, type ChangeAction } from './ResultActionBox'
import { ResultGrid, type TileAction } from './ResultGrid'
import { UploadImages } from './UploadImages'
import { ViewableMedia, viewerItem } from './ViewableMedia'

export type ActionBox = { action: TileAction; path: string } | null

export function StageResults({
  client,
  campaign,
  step,
  source,
  media,
  lists,
  busy,
  poster,
  copy,
  picking,
  box,
  proposedBox,
  onPick,
  onAction,
  onCloseBox,
  onSend,
  direct,
}: {
  client: AgentdClient | null
  campaign: CampaignDetail
  step: CampaignStep
  /** The image stage a video stage starts from. */
  source: CampaignStep | undefined
  media: Media
  lists: ModelLists | null
  busy: boolean
  /** A text ad's design stage: its results carry words. */
  poster: boolean
  copy: Record<string, string>
  /** The result a pick is being saved for. */
  picking: string
  box: ActionBox
  /** The box the agent proposed (a fix or a clip edit), pre-filled. */
  proposedBox: ActionBox
  onPick: (path: string) => void
  onAction: (action: TileAction, path: string) => void
  onCloseBox: () => void
  onSend: (text: string) => void
  /** Run a free, direct change and refresh the studio; its error shows on the stage. */
  direct: (what: () => Promise<void>) => void
}) {
  const id = campaign.campaign_id
  const proposal = step.proposal?.tool ? step.proposal : null
  const boxResult = box ? step.results.find((r) => r.path === box.path) : undefined
  const isSheet = step.action === 'sheet' || step.action === 'product_sheet'
  const empty = !step.results.length && <div className="stage-empty">Nothing made yet — generate on the right, or ask in the chat.</div>

  if (step.action === 'brief')
    return (
      <section className="stage-results">
        {campaign.brief ? <BriefView brief={campaign.brief} direction={campaign.direction} /> : <div className="stage-empty">No brief yet.</div>}
      </section>
    )

  if (isSheet)
    return (
      <section className="stage-results">
        <UploadImages client={client} campaign={id} step={step.id} label={step.action === 'product_sheet' ? 'Upload your own sheet' : 'Upload a sheet'}>
          <ResultGrid campaign={id} step={step} media={media} picking={picking} onPick={onPick} actions={[]} active={null} onAction={onAction} />
        </UploadImages>
        {empty}
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
      </section>
    )

  return (
    <section className="stage-results">
      {step.stale && (
        <div className="stale-note">
          <AlertTriangle size={14} /> The picked clip was made from an image {source?.title || 'its source step'} no longer picks. Generate a new one (it
          starts from the new pick), or keep this one.
        </div>
      )}
      {step.action === 'images' ? (
        <UploadImages client={client} campaign={id} step={step.id} label="Upload your own">
          <ResultGrid
            campaign={id}
            step={step}
            media={media}
            picking={picking}
            onPick={onPick}
            actions={poster ? ['text', 'fix', 'like'] : ['fix', 'like']}
            active={box}
            onAction={onAction}
          />
        </UploadImages>
      ) : (
        <ResultGrid campaign={id} step={step} media={media} picking={picking} onPick={onPick} actions={['edit', 'extend']} active={box} onAction={onAction} />
      )}
      {empty}

      {box?.action === 'text' && boxResult?.editable && (
        <PosterTextEditor key={box.path} client={client} campaign={id} step={step.id} design={box.path} picked={step.pick === box.path} media={media} onClose={onCloseBox} />
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
          onClose={onCloseBox}
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
              onClick={() => direct(() => updateStep(client!, id, step.id, { text: '' }))}
            >
              Drawn by the model
            </button>
            <button
              className={step.text === 'overlay' ? 'on' : ''}
              disabled={!client}
              title="The model makes the picture with empty space; the words are set on it in real fonts — always spelled right, editable for free"
              onClick={() => direct(() => updateStep(client!, id, step.id, { text: 'overlay' }))}
            >
              Set in real fonts (editable)
            </button>
          </div>
          {step.results.some((r) => r.editable) && (
            <button
              className="link-red"
              disabled={!client}
              title="Put the brief's current words on every real-font design (free)"
              onClick={() => direct(() => posterText(client!, { campaign: id, step: step.id, action: 'retext_all' }).then(() => undefined))}
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
          onClose={onCloseBox}
        />
      )}
    </section>
  )
}
