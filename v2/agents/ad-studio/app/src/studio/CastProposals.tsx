/* Cast members the agent proposed and nothing has been made for yet. With approval on "ask", a
 * new character sheet is generated only from here: the user checks the face and the description,
 * picks the model and presses Generate — the click records a one-time approval for exactly that
 * name and model, then sends the agent the exact cast_create call. Dismiss drops the proposal. */

import type { AgentdClient } from '@agentd/client'
import { Loader2, UserPlus, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { approveCast, command, dismissCast, listCast, type CastProposal, type Media, type ModelLists } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { ModelSelect } from './ModelSelect'

export function CastProposals({
  client,
  lists,
  running,
  onSend,
}: {
  client: AgentdClient | null
  lists: ModelLists | null
  running: boolean
  onSend: (text: string) => void
}) {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ proposed: CastProposal[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!client) return
    listCast(client)
      .then((d) => {
        setData({ proposed: d.proposed, media: d.media })
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  if (error) return <div className="studio-error">Could not read the proposed cast: {error}</div>
  if (!data?.proposed.length) return null
  return (
    <>
      {data.proposed.map((p) => (
        <ProposalCard key={p.name} client={client} proposal={p} media={data.media} lists={lists} running={running} onSend={onSend} />
      ))}
    </>
  )
}

function ProposalCard({
  client,
  proposal,
  media,
  lists,
  running,
  onSend,
}: {
  client: AgentdClient | null
  proposal: CastProposal
  media: Media
  lists: ModelLists | null
  running: boolean
  onSend: (text: string) => void
}) {
  const bump = useApp((s) => s.bumpStudio)
  const [model, setModel] = useState(proposal.model)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // A face to keep needs a model that takes a reference; a description alone, any image model.
  const models = (lists?.image.models || []).filter((m) => !proposal.references.length || (m.max_references ?? 1) >= 1)

  const act = async (what: 'generate' | 'dismiss') => {
    if (!client) return
    setBusy(true)
    setError('')
    try {
      if (what === 'dismiss') {
        await dismissCast(client, proposal.name)
        bump()
        return
      }
      const approval = await approveCast(client, proposal.name, model)
      const [provider, ...rest] = model.split('/')
      onSend(
        command(`New cast member ${proposal.name} on ${model}`, 'cast_create', {
          name: proposal.name,
          description: proposal.description,
          references: proposal.references,
          provider,
          model: rest.join('/'),
          approval,
        }),
      )
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="act-box proposed cast-proposal">
      <div className="gen-panel-head">
        <span className="strip-label">Proposed cast member — check it, pick the model, then Generate</span>
        <button className="ref-x" onClick={() => void act('dismiss')} disabled={busy} title="Dismiss">
          <X size={11} />
        </button>
      </div>
      <div className="cast-proposal-body">
        {proposal.references.map((r) => (
          <img key={r} className="cast-proposal-face" src={media(r)} alt="" />
        ))}
        <div>
          <span className="camp-name">{proposal.name}</span>
          <p className="cast-desc">{proposal.description}</p>
        </div>
      </div>
      <div className="model-pick">
        {lists && <ModelSelect label="Model" value={model} models={models} disabled={busy} onChange={setModel} />}
        <button className="prime-btn gen-go" disabled={busy || running || !client || !model} onClick={() => void act('generate')}>
          {busy ? <Loader2 size={14} className="spin" /> : <UserPlus size={14} />} Generate
        </button>
      </div>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
