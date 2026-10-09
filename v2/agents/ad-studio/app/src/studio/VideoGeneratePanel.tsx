/* Make a clip for a video step — from any image, as many times as you like, each run adding to the
 * step.
 *
 * The first frame starts as the source step's pick; choose another from that step's images or any
 * image you selected — or upload your own (it joins the source step's results, and is chosen).
 * Prompt and references start as the step's — or as the agent PROPOSED, marked as such; model,
 * length and resolution as its last run's. Generate first approves exactly this run (model,
 * length, resolution, first frame; a one-time token), then sends it as the exact call. */

import type { AgentdClient } from '@agentd/client'
import { Film, Loader2, RotateCcw, X } from 'lucide-react'
import { useState } from 'react'

import { approveRun, command, dismissProposal, isVideo, type CampaignStep, type Media, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { MediaTileActions } from './MediaTileActions'
import { ModelSelect } from './ModelSelect'
import { ReferenceTray } from './ReferenceTray'
import { UploadImages } from './UploadImages'

export function VideoGeneratePanel({
  client,
  campaign,
  step,
  source,
  media,
  lists,
  busy,
  onSend,
}: {
  client: AgentdClient | null
  campaign: string
  step: CampaignStep
  /** The image step it starts from. */
  source: CampaignStep | undefined
  media: Media
  lists: ModelLists | null
  busy: boolean
  onSend: (text: string) => void
}) {
  const selection = useApp((s) => s.selection)
  const bump = useApp((s) => s.bumpStudio)
  const proposed = step.proposal?.tool === 'step_run' ? step.proposal.args || {} : null
  const [frame, setFrame] = useState<string>(proposed?.first_frame || '')
  const [prompt, setPrompt] = useState<string>(proposed?.prompt || step.defaults.prompt)
  const [refs, setRefs] = useState<string[]>(proposed?.references || step.defaults.references)
  const [model, setModel] = useState<string>(proposed?.model || step.model || lists?.video.default || '')
  const [seconds, setSeconds] = useState<number>(Number(proposed?.seconds) || step.seconds || step.defaults.seconds || 5)
  const [res, setRes] = useState<string>(proposed?.resolution || step.resolution)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

  const chosen = model || lists?.video.default || ''
  const spec = lists?.video.models.find((m) => m.id === chosen)
  const range = spec?.durations || null
  const resolutions = Object.keys(spec?.resolutions || {})
  const resolution = resolutions.length === 1 ? resolutions[0] : resolutions.includes(res) ? res : resolutions.includes('720p') ? '720p' : resolutions[0] || res
  const lengthOk = !range || (seconds >= range.min && seconds <= range.max)

  const sourcePick = source?.pick || ''
  const firstFrame = frame || sourcePick
  // Every image it could start from: the source step's, then any image selected elsewhere.
  const frames = Array.from(
    new Set([
      ...(sourcePick ? [sourcePick] : []),
      ...(source?.results || []).filter((r) => !isVideo(r.path)).map((r) => r.path),
      ...selection.filter((s) => s.kind === 'image' && !isVideo(s.path)).map((s) => s.path),
    ]),
  )
  const ownPrompt = prompt.trim() !== step.defaults.prompt.trim()
  const ownRefs = refs.join('|') !== step.defaults.references.join('|')

  const generate = async () => {
    if (!client) return
    setSending(true)
    setError('')
    const ownFrame = frame && frame !== sourcePick ? frame : ''
    try {
      const approval = await approveRun(client, campaign, step.id, 'step_run', {
        model: chosen,
        seconds,
        resolution,
        first_frame: ownFrame,
      })
      onSend(
        command(
          `${campaign} · ${step.title}: a ${seconds} s ${resolution} clip on ${chosen}${ownFrame ? ' from the image I chose' : ''}${ownPrompt ? ' from my prompt' : ''}`,
          'step_run',
          {
            campaign,
            step: step.id,
            model: chosen,
            seconds,
            resolution,
            ...(ownFrame ? { first_frame: ownFrame } : {}),
            ...(ownPrompt ? { prompt: prompt.trim() } : {}),
            ...(ownRefs ? { references: refs } : {}),
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
        <span className="strip-label">{proposed ? 'Proposed by the agent — check the model, then Generate' : 'Generate a clip'}</span>
        {proposed && client && (
          <button className="ref-add" onClick={() => void dismissProposal(client, campaign, step.id).then(bump)} title="Not this one">
            <X size={11} /> dismiss
          </button>
        )}
        {!proposed && ownPrompt && (
          <button className="ref-add" onClick={() => setPrompt(step.defaults.prompt)} title="Back to the step's prompt">
            <RotateCcw size={11} /> step's prompt
          </button>
        )}
      </div>
      <div className="another">
        <span className="strip-label">First frame{source ? ` — from ${source.title}, anything you selected, or your own` : ''}</span>
        {frames.length ? (
          <div className="another-row">
            {frames.map((p) => (
              <button key={p} className={`another-still${p === firstFrame ? ' on' : ''}`} onClick={() => setFrame(p)} title={p}>
                <img src={media(p)} alt="" loading="lazy" />
                <MediaTileActions item={{ path: p, kind: 'image', campaign: '', shot: '', src: media(p) }} title={p.split('/').pop() || p} selectable={false} />
                {p === firstFrame && <span className="another-tag on">first frame</span>}
                {p !== firstFrame && p === sourcePick && <span className="another-tag">picked</span>}
              </button>
            ))}
          </div>
        ) : (
          <div className="ref-none">No image yet — make or pick one in {source?.title || 'the image step'}, select one, or upload your own.</div>
        )}
        {source && (
          <UploadImages
            client={client}
            campaign={campaign}
            step={source.id}
            label="Upload a first frame"
            multiple={false}
            onDone={(paths) => paths[0] && setFrame(paths[0])}
          />
        )}
      </div>
      <textarea className="gen-prompt" rows={3} value={prompt} disabled={busy} placeholder="Describe the motion — what she does, how the camera moves" onChange={(e) => setPrompt(e.target.value)} />
      <ReferenceTray value={refs} defaults={step.defaults.references} media={media} onChange={setRefs} />
      <div className="model-pick">
        {lists && (
          <ModelSelect
            label="Model"
            value={chosen}
            models={lists.video.models}
            disabled={busy}
            onChange={(m) => {
              setModel(m)
              setRes('')
            }}
          />
        )}
        <label className="model-field">
          <span className="strip-label">Resolution</span>
          <select value={resolution} disabled={busy || resolutions.length < 2} onChange={(e) => setRes(e.target.value)}>
            {(resolutions.length ? resolutions : [resolution || '720p']).map((r) => (
              <option key={r} value={r}>
                {r}
                {spec?.resolutions?.[r] ? ` — ${spec.resolutions[r]}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="model-field">
          <span className="strip-label">Length (s)</span>
          <input type="number" min={range?.min} max={range?.max} value={seconds} disabled={busy} onChange={(e) => setSeconds(Number(e.target.value) || 0)} />
          {!lengthOk && range && <span className="field-err">{range.min}–{range.max} s on this model</span>}
        </label>
        <button
          className="prime-btn gen-go"
          disabled={busy || sending || !client || !firstFrame || !prompt.trim() || !chosen || !lengthOk}
          onClick={() => void generate()}
          title={`Approve ${chosen} and generate`}
        >
          {sending ? <Loader2 size={14} className="spin" /> : <Film size={14} />} Generate
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
