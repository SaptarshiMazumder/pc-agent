/* The studio — the left of a chat (the chat is on its right): the campaign this chat is on, as a
 * pipeline of stages, the stage on screen, and how it hands on to the next.
 *
 *   CampaignHeader    what it is, how runs are approved, the budget
 *   StagePipeline     every stage, its pick, and "All generations"
 *   StepPanel         the stage on screen: its results | the form that makes more
 *   CarryForwardBar   its pick, and the next stage
 *
 * A chat with no campaign yet shows the new-ad setup (or a post chat's collections, or its post).
 * The pipeline opens on the current stage (the first not done) and follows it as the campaign
 * moves, unless the user opened another. */

import type { AgentdClient } from '@agentd/client'
import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listModels, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { AddProductStill } from './AddProductStill'
import { CampaignHeader } from './CampaignHeader'
import { CarryForwardBar } from './CarryForwardBar'
import { CastProposals } from './CastProposals'
import { DoneView } from './DoneView'
import { GenerationsTab } from './GenerationsTab'
import { NewAdSetup } from './NewAdSetup'
import { PostOpening } from './PostOpening'
import { PostPanel } from './PostPanel'
import { StagePipeline } from './StagePipeline'
import { StepPanel } from './StepPanel'
import { useCampaign } from './useCampaign'
import { usePost } from './usePost'

const NO_JOBS: Record<string, { tool: string; text: string }> = {}

export function Studio({
  client,
  connected,
  session,
  running,
  onAnswer,
  onFiles,
}: {
  client: AgentdClient | null
  /** The socket is open — the model lists are (re)loaded when it is. */
  connected: boolean
  session: string
  running: boolean
  onAnswer: (text: string) => void
  /** Attach files to the chat's next message (a new ad's product photos). */
  onFiles: (files: FileList | File[]) => void
}) {
  const tick = useApp((s) => s.studioTick)
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
  const [all, setAll] = useState(false)
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

  // A different campaign, or the campaign moving on, shows its current stage again.
  useEffect(() => setShown(''), [campaign?.campaign_id, campaign?.current])
  // A run the agent proposed opens its stage, where the user approves it.
  const proposedStep = campaign?.steps.find((s) => s.proposal?.tool)?.id || ''
  useEffect(() => {
    if (proposedStep) {
      setShown(proposedStep)
      setAll(false)
    }
  }, [proposedStep])

  const show = (id: string) => {
    setShown(id)
    setAll(false)
  }
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
        !loading && <NewAdSetup client={client} session={session} onFiles={onFiles} />
      ) : (
        <>
          <CampaignHeader client={client} campaign={campaign} media={media} />
          <StagePipeline
            steps={campaign.steps}
            current={campaign.current}
            shown={step?.id || ''}
            all={all}
            media={media}
            onShow={show}
            onAll={() => setAll(true)}
          />
          <AddProductStill client={client} campaign={campaign.campaign_id} onAdded={show} />

          {listsError && (
            <div className="studio-error">
              Could not load the model lists, so the model dropdowns are missing: {listsError}. If the daemon was not restarted after Ad Studio was
              updated, restart it.
            </div>
          )}

          {all ? (
            <GenerationsTab client={client} campaign={campaign.campaign_id} />
          ) : (
            <>
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
                />
              )}
              {!campaign.current && <DoneView campaign={campaign} media={media} />}
              {step && <CarryForwardBar campaign={campaign} step={step} media={media} onShow={show} />}
            </>
          )}
        </>
      )}
    </section>
  )
}
