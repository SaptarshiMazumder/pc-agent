/* A stage's choices — what its template asks the user for (who, the shape, where, the light…) — and
 * the stage's own Auto switch.
 *
 * MANUAL BY DEFAULT: a required choice nobody made is marked, and the stage will not run until it is
 * made — unless Auto is on (the campaign's switch, or this stage's own), when the agent fills it.
 * Every change is saved at once (step_update, free — nothing is generated).
 *
 * `homes` narrows it to the choices this form owns: a Generate panel already asks for its own count,
 * model, length and resolution, so the image and clip stages show only the rest here. */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import { chooseSettings, setAutoPick, type CampaignDetail, type CampaignStep, type CastMember, type ModelLists, type StepSetting } from '../agentd/campaigns'
import { ModelSelect } from './ModelSelect'

export function StepSettingsForm({
  client,
  campaign,
  step,
  cast,
  lists,
  busy,
  homes,
  direct,
}: {
  client: AgentdClient | null
  campaign: CampaignDetail
  step: CampaignStep
  cast: CastMember[]
  lists: ModelLists | null
  busy: boolean
  /** The settings shown (by home); all of them when left out. */
  homes?: StepSetting['home'][]
  direct: (what: () => Promise<void>) => void
}) {
  const id = campaign.campaign_id
  const shown = step.settings.filter((s) => !homes || homes.includes(s.home))
  const save = (key: string, value: string) => direct(() => chooseSettings(client!, id, step.id, { [key]: value }))
  const own = step.auto === null ? 'campaign' : step.auto ? 'on' : 'off'

  return (
    <div className="step-choices">
      <div className="step-choices-head">
        <span className="strip-label">Your choices</span>
        <div className="seg-switch small" role="radiogroup" aria-label="This stage's Auto switch">
          {(
            [
              ['campaign', `Campaign's (${campaign.auto_pick ? 'Auto' : 'Manual'})`, 'Follow the campaign\'s switch'],
              ['off', 'Manual', 'You make every choice for this stage'],
              ['on', 'Auto', 'The agent fills the choices you leave empty in this stage'],
            ] as const
          ).map(([mode, label, title]) => (
            <button
              key={mode}
              role="radio"
              aria-checked={own === mode}
              className={own === mode ? 'on' : ''}
              disabled={!client || busy}
              title={title}
              onClick={() => direct(() => setAutoPick(client!, id, mode === 'on', step.id, mode === 'campaign'))}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="step-choices-none">Nothing to choose here beyond the panel below.</p>
      ) : (
        <div className="step-choices-grid">
          {shown.map((s) => (
            <SettingField
              key={`${s.key}:${s.value || ''}`}
              setting={s}
              auto={step.auto_on}
              cast={cast}
              lists={lists}
              disabled={!client || busy}
              onSave={(v) => save(s.key, v)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SettingField({
  setting: s,
  auto,
  cast,
  lists,
  disabled,
  onSave,
}: {
  setting: StepSetting
  auto: boolean
  cast: CastMember[]
  lists: ModelLists | null
  disabled: boolean
  onSave: (value: string) => void
}) {
  const [draft, setDraft] = useState(s.value || '')
  useEffect(() => setDraft(s.value || ''), [s.value])
  const commit = (v: string) => {
    if (v.trim() !== (s.value || '')) onSave(v.trim())
  }
  // With Auto on, an empty choice says what the agent will do with it.
  const empty = auto ? `Auto: ${s.suggested || 'the agent picks'}` : s.required ? 'Choose…' : 'Leave empty and the brief decides'
  const label = (
    <span className="strip-label">
      {s.label}
      {s.missing && !auto && <span className="choice-needed"> · needed</span>}
    </span>
  )

  let control
  if (s.kind === 'aspect') {
    control = (
      <div className="seg-switch small" role="radiogroup" aria-label={s.label}>
        {s.options.map((o) => (
          <button key={o} role="radio" aria-checked={s.value === o} className={s.value === o ? 'on' : ''} disabled={disabled} onClick={() => onSave(s.value === o ? '' : o)}>
            {o}
          </button>
        ))}
      </div>
    )
  } else if (s.kind === 'cast') {
    control = (
      <select value={s.value || ''} disabled={disabled} onChange={(e) => onSave(e.target.value)}>
        <option value="">{empty}</option>
        {cast.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
    )
  } else if (s.kind === 'model') {
    const models = (s.key === 'video_model' ? lists?.video.models : lists?.image.models) || []
    return (
      <div className={`step-choice${s.missing && !auto ? ' needed' : ''}`}>
        <ModelSelect label={s.label} value={s.value || ''} models={models} placeholder={empty} disabled={disabled} onChange={onSave} />
      </div>
    )
  } else if (s.kind === 'resolution') {
    control = (
      <select value={s.value || ''} disabled={disabled} onChange={(e) => onSave(e.target.value)}>
        <option value="">{empty}</option>
        {s.options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    )
  } else if (s.kind === 'number') {
    control = (
      <input
        type="number"
        min={s.minimum || undefined}
        max={s.maximum || undefined}
        value={draft}
        placeholder={empty}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => commit(draft)}
      />
    )
  } else {
    // choice / text: free words; a choice offers its options as suggestions.
    const list = s.options.length ? `opts-${s.key}` : undefined
    control = (
      <>
        <input
          value={draft}
          list={list}
          placeholder={empty}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => commit(draft)}
          onKeyDown={(e) => e.key === 'Enter' && commit(draft)}
        />
        {list && (
          <datalist id={list}>
            {s.options.map((o) => (
              <option key={o} value={o} />
            ))}
          </datalist>
        )}
      </>
    )
  }

  return (
    <label className={`step-choice${s.missing && !auto ? ' needed' : ''}`} title={s.help}>
      {label}
      {control}
    </label>
  )
}
