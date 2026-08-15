/* The settings page: two layers of config, the keys that pay for it, and the services it reaches.
 *
 * See agentd/settings.ts for the layering rules — which layer wins, what a patch contains, and why
 * a provider key can be written but never read back.
 */

import type { AgentdClient } from '@agentd/client'
import { usePlatform, useRestartDaemon } from '../../agentd/platform'
import { useServices } from '../../agentd/services'
import { useSettings } from '../../agentd/settings'
import { AccountSection } from './AccountSection'
import { DeclaredField } from './DeclaredField'
import { Field } from './Field'
import { ModeSection } from './ModeSection'
import { RestartSection } from './RestartSection'
import { SecretField } from './SecretField'
import { ServicesSection } from './ServicesSection'
import { COST_EFFICIENCY_MODELS, COST_EFFICIENCY_TOGGLE, GROUPS } from './groups'

export function SettingsView({ client }: { client: AgentdClient }) {
  const s = useSettings(client)
  const platform = usePlatform(client)
  const services = useServices(client)
  const daemon = useRestartDaemon(client)

  if (s.loadError) {
    return (
      <div className="settings-scroll">
        <div className="settings-inner">
          <div className="loading">could not load settings: {s.loadError}</div>
        </div>
      </div>
    )
  }

  if (!s.data) {
    return (
      <div className="settings-scroll">
        <div className="settings-inner">
          <div className="loading">loading settings…</div>
        </div>
      </div>
    )
  }

  const data = s.data
  const pinned = data.envOverrides || {}
  const values = data.values || {}
  // ABSENT for an installed agent — presence is the whole test for "may this page reveal a key".
  const revealable = Object.prototype.hasOwnProperty.call(data, 'envValues')

  return (
    <div className="settings-scroll">
      <div className="settings-inner">
        {/* Account first, Run mode second — that is the order they depend on. Cloud needs somebody
            to bill, so the second control greys out until the first one is filled in. */}
        <AccountSection
          auth={platform.auth}
          error={platform.error}
          onSignIn={platform.signIn}
          onSignOut={platform.signOut}
        />
        <ModeSection
          auth={platform.auth}
          chosen={platform.chosen}
          error={platform.error}
          onSwitch={(next) => {
            s.setMessage({ text: `switching to ${next === 'cloud' ? 'Cloud' : 'Local'}…`, tone: '' })
            void platform
              .switchMode(next)
              .then(() =>
                s.setMessage({
                  text: `Now running in ${next === 'cloud' ? 'Cloud' : 'Local'} mode.`,
                  tone: 'ok',
                }),
              )
              .catch((e) =>
                s.setMessage({ text: `could not switch: ${String(e?.message || e)}`, tone: 'bad' }),
              )
          }}
        />
        <ServicesSection
          servers={services.servers}
          connections={services.connections}
          onApprove={services.approve}
          onConnect={services.connect}
          onDisconnect={services.disconnect}
        />

        {GROUPS.map((group) => {
          // Nothing declared -> no group. An empty "What this agent needs" reads as a page that
          // failed to load its fields.
          if (group.declared && !(data.settings || []).length) return null

          return (
            <section className="set-group" key={group.title}>
              <h2>{group.title}</h2>
              {group.help && <p className="ghelp">{group.help}</p>}

              {/* The override flag. Moving it re-renders the whole group, because every row in it
                  changes layer — showing the old layer's values afterwards would be a lie. */}
              {group.agentToggle && (
                <div className="field override">
                  <div>
                    <label>Override JARVIS settings</label>
                    <span className="fhelp">
                      {s.overriding
                        ? "On — this agent's own values win, one knob at a time."
                        : "Off — the daemon's values are in force. Anything set here is kept, just dormant."}
                    </span>
                  </div>
                  <button
                    className={`toggle ${s.overriding ? 'on' : ''}`}
                    onClick={() => s.setOverride(!s.overriding)}
                  />
                </div>
              )}

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
                  // Leaving the dropdown on screen would display a value with no effect, which is
                  // the exact confusion this page exists to end.
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
                    onChange={(v) => s.setValue({ ...COST_EFFICIENCY_TOGGLE, agent: !!group.agent }, v)}
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

        {/* Last, and after the declared fields on purpose: it is the thing you reach for once
            everything else is set and the daemon is still serving what it loaded at boot. */}
        <RestartSection
          onRestart={() => void daemon.restart()}
          busy={daemon.busy}
          note={daemon.note}
        />

        <div className="set-bar">
          <span className={`set-msg ${s.message.tone}`}>
            {s.message.text ||
              (data.keysLocked
                ? 'Provider keys are managed by the platform on this install.'
                : `agentd ${data.version || ''} · ${data.effectiveModel || ''}`)}
          </span>
          <button className="prime-btn" disabled={!s.dirty} onClick={() => void s.commit()}>
            Save changes
          </button>
        </div>
      </div>
    </div>
  )
}
