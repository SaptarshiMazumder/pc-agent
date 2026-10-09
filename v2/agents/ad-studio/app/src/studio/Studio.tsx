/* The studio — the right-hand side of a chat: the campaign this chat is on, its steps, and what the
 * cast member and the product look like.
 *
 * The stepper is the campaign's checklist (the recipe's steps plus any the user added); any step
 * opens, any time. It opens on the current step (the first not done) and follows it as the
 * campaign moves, unless the user opened another. */

import type { AgentdClient } from '@agentd/client'
import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listModels, money, setApproval, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { AddProductStill } from './AddProductStill'
import { DoneView } from './DoneView'
import { GenerationsTab } from './GenerationsTab'
import { MediaTileActions } from './MediaTileActions'
import { StepPanel } from './StepPanel'
import { StepStepper } from './StepStepper'
import { CastProposals } from './CastProposals'
import { PostOpening } from './PostOpening'
import { PostPanel } from './PostPanel'
import { StartPanel } from './StartPanel'
import { usePost } from './usePost'
import { useCampaign } from './useCampaign'

const NO_JOBS: Record<string, { tool: string; text: string }> = {}

export function Studio({
  client,
  connected,
  session,
  running,
  onAnswer,
}: {
  client: AgentdClient | null
  /** The socket is open — the model lists are (re)loaded when it is. */
  connected: boolean
  session: string
  running: boolean
  onAnswer: (text: string) => void
}) {
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const pinned = useApp((s) => s.pinned[session] || '')
  // The selector returns the stored object itself (or undefined) — never a fresh `{}`, which
  // zustand would see as a change on every render and loop on.
  const jobs = useApp((s) => s.sessions[session]?.jobs) ?? NO_JOBS
  const { campaign, media, error, loading } = useCampaign(client, session, pinned)
  // A post chat: the post it is making (or, before it exists, the collections to make it from).
  const postChat = useApp((s) => s.starts[session]?.mode === 'post')
  const pinnedPost = useApp((s) => s.pinnedPost[session] || '')
  const { post, media: postMedia, progress: postProgress, error: postError } = usePost(client, session, pinnedPost)
  const working = Object.values(jobs)
  const [tab, setTab] = useState<'steps' | 'generations'>('steps')
  const [shown, setShown] = useState('')

  /* THE MODELS, loaded once: the agent's specs. Said, not swallowed, when they cannot be read —
     most often a daemon still running older Ad Studio code (restart it). */
  const [lists, setLists] = useState<ModelLists | null>(null)
  const [listsError, setListsError] = useState('')
  // Loaded when the connection opens — and tried again on every studio refresh until it works: a
  // window that opened before the socket did used to stay without dropdowns for good.
  useEffect(() => {
    if (!client || !connected || lists) return
    listModels(client)
      .then((l) => {
        setLists(l)
        setListsError('')
      })
      .catch((e) => setListsError(String(e?.message || e)))
  }, [client, connected, lists, tick])

  // A different campaign, or the campaign moving on, shows its current step again.
  useEffect(() => setShown(''), [campaign?.campaign_id, campaign?.current])
  // A run the agent proposed opens its step, where the user approves it.
  const proposedStep = campaign?.steps.find((s) => s.proposal?.tool)?.id || ''
  useEffect(() => {
    if (proposedStep) setShown(proposedStep)
  }, [proposedStep])

  const step = campaign
    ? campaign.steps.find((s) => s.id === (shown || campaign.current)) || campaign.steps[campaign.steps.length - 1]
    : undefined

  return (
    <section className="studio">
      {working.length > 0 && (
        <div className="job-strip">
          {working.map((j, i) => (
            <span key={i} className="job">
              <Loader2 size={13} className="spin" /> {j.text || `${j.tool} working…`}
            </span>
          ))}
        </div>
      )}

      {error && <div className="studio-error">Could not read the campaign: {error}</div>}

      <CastProposals client={client} lists={lists} running={running} onSend={onAnswer} />

      {postError && <div className="studio-error">Could not read the post: {postError}</div>}
      {post ? (
        <PostPanel key={post.slug} client={client} post={post} media={postMedia} progress={postProgress} running={running} onSend={onAnswer} />
      ) : postChat && !campaign ? (
        <PostOpening client={client} session={session} />
      ) : !campaign ? (
        !loading && <StartPanel client={client} session={session} />
      ) : (
        <>
          <header className="studio-head">
            <div className="studio-title">
              <span className="eyebrow-red">{campaign.recipe_title || campaign.recipe_key}</span>
              <h2>{campaign.product.name}</h2>
              <span className="studio-sub">
                {campaign.campaign_id}
                {campaign.cast ? ` · ${campaign.cast.name}` : ''}
              </span>
            </div>
            <div className="approval-switch" role="radiogroup" aria-label="Model approval">
              <span className="strip-label">Model approval</span>
              {(['ask', 'auto'] as const).map((mode) => (
                <button
                  key={mode}
                  role="radio"
                  aria-checked={campaign.approval === mode}
                  className={`filter-chip${campaign.approval === mode ? ' on' : ''}`}
                  disabled={!client}
                  onClick={() => client && void setApproval(client, campaign.campaign_id, mode).then(bump)}
                  title={
                    mode === 'ask'
                      ? 'Nothing is generated until you press Generate here — that click approves its model'
                      : 'The agent generates when you ask it in the chat'
                  }
                >
                  {mode === 'ask' ? 'Ask me' : 'Auto'}
                </button>
              ))}
            </div>
            <div className="studio-money" title="Spent so far / the campaign's budget">
              <span className="money-now">{money(campaign.spent_usd)}</span>
              <span className="money-of">of {money(campaign.budget_usd)}</span>
            </div>
          </header>

          <div className="studio-tabs" role="tablist">
            <button className={`studio-tab${tab === 'steps' ? ' on' : ''}`} onClick={() => setTab('steps')}>
              Steps
            </button>
            <button className={`studio-tab${tab === 'generations' ? ' on' : ''}`} onClick={() => setTab('generations')}>
              Generations
            </button>
          </div>

          {listsError && (
            <div className="studio-error">
              Could not load the model lists, so the model dropdowns are missing: {listsError}. If the daemon was not restarted after
              Ad Studio was updated, restart it.
            </div>
          )}

          {tab === 'generations' ? (
            <GenerationsTab client={client} campaign={campaign.campaign_id} />
          ) : (
            <>
              <div className="steps-bar">
                <StepStepper steps={campaign.steps} current={campaign.current} shown={step?.id || ''} onShow={setShown} />
              </div>
              <AddProductStill client={client} campaign={campaign.campaign_id} onAdded={setShown} />
              {!campaign.current && <DoneView campaign={campaign} media={media} />}
              {step && (
                <StepPanel
                  key={`${campaign.campaign_id}:${step.id}`}
                  client={client}
                  campaign={campaign}
                  step={step}
                  media={media}
                  lists={lists}
                  busy={running}
                  onSend={onAnswer}
                  onShow={setShown}
                />
              )}
            </>
          )}

          <div className="studio-strip">
            {campaign.cast && (
              <div className="strip-card">
                <span className="strip-label">Cast · {campaign.cast.name}</span>
                <div className="sheet-box">
                  <img src={media(campaign.cast.sheet)} alt={campaign.cast.name} />
                  <MediaTileActions
                    item={{ path: campaign.cast.sheet, kind: 'image', campaign: campaign.campaign_id, shot: '', src: media(campaign.cast.sheet) }}
                    title={`Cast · ${campaign.cast.name}`}
                  />
                </div>
              </div>
            )}
            <div className="strip-card">
              <span className="strip-label">Product · {campaign.product.category}</span>
              <div className="strip-photos">
                {campaign.product.photos.map((p) => (
                  <div key={p} className="sheet-box">
                    <img src={media(p)} alt="product" />
                    <MediaTileActions item={{ path: p, kind: 'image', campaign: campaign.campaign_id, shot: '', src: media(p) }} title="Product photo" />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
