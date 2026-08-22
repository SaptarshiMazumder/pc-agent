/* The settings page every agent gets.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. If your agent needs a knob this does not show, add it to `schema.ts` there — every agent
 * then gets it, which is the point.
 *
 * SAME PAGE AS THE ASSISTANT'S. A user who configures the assistant and then opens one of your
 * agents should not meet a different, smaller page with different names for the same things. The
 * only difference is that an agent's own values win over the daemon's, and every row says which
 * layer it came from.
 *
 * SAVE RESTARTS THE DAEMON when the daemon says its running copy is now stale. It used to print
 * "restart for some of these to take effect" and leave it there — a save reporting success while
 * the process kept serving the old value. A message is not a mechanism.
 *
 * WHAT IT DOES NOT RENDER: sign-in and credits. Those are their own screens (`common/auth`,
 * `common/credits`) because they are what a user comes looking for when a run stops, and a fix
 * buried three scrolls into a config page is a fix nobody finds.
 */

import type { AgentdClient } from '@agentd/client'
import { useState } from 'react'
import { DeclaredField } from './DeclaredField'
import { Field } from './Field'
import { SecretField } from './SecretField'
import { COST_EFFICIENCY_MODELS, COST_EFFICIENCY_TOGGLE, GROUPS } from './schema'
import { useSettings } from './useSettings'

export function Settings({
  client,
  agentId,
  onRestart,
}: {
  client: AgentdClient
  /** Whose layer this page edits. Each agent knows its own id — see useSettings. */
  agentId: string
  /** Restart the daemon. Optional: without it a save that needs a restart says so instead of
   *  doing it, which is honest but worse. Pass it if your window can restart the daemon. */
  onRestart?: () => Promise<void>
}) {
  const s = useSettings(client, agentId)
  const [restarting, setRestarting] = useState(false)

  async function saveChanges(): Promise<void> {
    const needsRestart = await s.commit()
    if (!needsRestart || !onRestart) return
    setRestarting(true)
    try {
      await onRestart()
    } finally {
      setRestarting(false)
    }
  }

  if (s.loadError) {
    return <div className="settings-scroll"><div className="settings-inner">
      <div className="loading">could not load settings: {s.loadError}</div>
    </div></div>
  }
  if (!s.data) {
    return <div className="settings-scroll"><div className="settings-inner">
      <div className="loading">loading settings…</div>
    </div></div>
  }

  const data = s.data
  const pinned = data.envOverrides || {}
  const values = data.values || {}
  // ABSENT for an installed agent — its presence is the whole test for "may this page reveal a
  // provider key". A missing field is not the same as an empty one.
  const revealable = Object.prototype.hasOwnProperty.call(data, 'envValues')

  return (
    <div className="settings-scroll">
      <div className="settings-inner">
        {GROUPS.map((group) => {
          // Nothing declared -> no group. An empty "What this agent needs" reads as a page that
          // failed to load its fields.
          if (group.declared && !(data.settings || []).length) return null

          return (
            <section className="set-group" key={group.title}>
              <h2>{group.title}</h2>
              {group.help && <p className="ghelp">{group.help}</p>}

              {group.declared &&
                (data.settings || []).map((f) => (
                  <DeclaredField
                    key={f.key}
                    field={f}
                    isSet={!!(data.env || {})[f.key]}
                    value={(data.settingsValues || {})[f.key] || ''}
                    onChange={(v) => s.setKey(f.key, v)}
                  />
                ))}

              {group.secrets &&
                (data.providerKeys || []).map((name) => (
                  <SecretField
                    key={name}
                    name={name}
                    isSet={!!(data.env || {})[name]}
                    value={(data.envValues || {})[name] || ''}
                    revealable={revealable}
                    locked={!!data.keysLocked}
                    onChange={(v) => s.setKey(name, v)}
                  />
                ))}

              {!group.declared &&
                !group.secrets &&
                (group.fields || []).map((f) => {
                  // The daemon does not expose it -> do not invent a control for it.
                  if (!(f.key in values)) return null
                  // With cost efficiency ON, `model` is NOT what runs — the router picks per turn.
                  // Leaving the dropdown on screen would show a value with no effect, which is the
                  // exact confusion this page exists to end.
                  if (group.costEfficiency && f.key === 'model' && s.costEfficiencyOn(!!group.agent))
                    return null
                  const spec = { ...f, agent: !!group.agent }
                  return (
                    <Field
                      key={f.key}
                      spec={spec}
                      value={s.valueOf(spec)}
                      source={group.agent ? s.sourceOf(spec) : undefined}
                      pinnedBy={pinned[f.key]}
                      catalog={f.catalog ? (data.catalogs || {})[f.catalog] : undefined}
                      onChange={(v) => s.setValue(spec, v)}
                    />
                  )
                })}

              {/* Cost efficiency last in its group: it CHANGES what the Model row above means, so
                  it reads as a modifier of the section rather than a knob of its own. */}
              {group.costEfficiency && (
                <>
                  <Field
                    spec={{ ...COST_EFFICIENCY_TOGGLE, agent: !!group.agent }}
                    value={s.valueOf({ ...COST_EFFICIENCY_TOGGLE, agent: !!group.agent })}
                    onChange={(v) =>
                      s.setValue({ ...COST_EFFICIENCY_TOGGLE, agent: !!group.agent }, v)
                    }
                  />
                  {s.costEfficiencyOn(!!group.agent) &&
                    COST_EFFICIENCY_MODELS.map((f) => {
                      const spec = { ...f, agent: !!group.agent }
                      return (
                        <Field
                          key={f.key}
                          spec={spec}
                          value={s.valueOf(spec)}
                          catalog={(data.catalogs || {})[f.catalog!]}
                          onChange={(v) => s.setValue(spec, v)}
                        />
                      )
                    })}
                </>
              )}
            </section>
          )
        })}

        <div className="set-bar">
          <span className={`set-msg ${s.message.tone}`}>
            {s.message.text ||
              (data.keysLocked
                ? 'Provider keys are managed by the platform on this install.'
                : `agentd ${data.version || ''}`)}
          </span>
          <button
            className="prime-btn"
            disabled={!s.dirty || restarting}
            onClick={() => void saveChanges()}
          >
            {restarting ? 'Restarting…' : 'Save changes'}
          </button>
        </div>
      </div>
    </div>
  )
}
