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

import './settings.css'
import { useState, type ReactNode } from 'react'
import { DeclaredField } from './DeclaredField'
import { Field } from './Field'
import { SecretField } from './SecretField'
import { SettingsActionsContext } from './SettingsActions'
import { COST_EFFICIENCY_MODELS, COST_EFFICIENCY_TOGGLE, GROUPS, TABS, type TabId } from './schema'
import { useSettings } from './useSettings'

export function Settings({
  client,
  agentId,
  onRestart,
  onSaved,
  icons,
  extras,
}: {
  client: AgentdClient
  /** Whose layer this page edits. Each agent knows its own id — see useSettings. */
  agentId: string
  /** Restart the daemon. Optional: without it a save that needs a restart says so instead of
   *  doing it, which is honest but worse. Pass it if your window can restart the daemon. */
  onRestart?: () => Promise<void>
  /** Called after every successful save, from the Save button and from an `extras` control alike.
   *
   *  For a window that shows a declared value somewhere ELSE — a server URL in the top bar, a
   *  "not configured yet" badge in the nav — which would otherwise still be showing what was
   *  there when the window connected. The page cannot know what else is on screen, and a stale
   *  chip beside a freshly saved field is the kind of wrong that gets believed. */
  onSaved?: () => void
  /** An icon per tab id, for a window that has an icon set. Optional on purpose — see TABS. */
  icons?: Partial<Record<TabId, ReactNode>>
  /** Rendered at the top of the tab it names, above that tab's groups. For the things a window
   *  has that the shared schema cannot know about — a run-mode switch, its MCP servers, a restart
   *  control, a "Test connection" button beside a URL the agent declared in `[[settings]]`.
   *  Without this they would have to live outside the page and it would stop being one page.
   *
   *  A control in here can call `useSettingsActions()` to save the page before it acts — which is
   *  what a Test button must do, because the tool it calls reads the saved environment and not
   *  this form. */
  extras?: Partial<Record<TabId, ReactNode>>
}) {
  const s = useSettings(client, agentId)
  const [restarting, setRestarting] = useState(false)
  const [tab, setTab] = useState<TabId>('general')

  /** Save, and tell the window. Everything that writes goes through here — the Save button
   *  below and any `extras` control that saves before it acts — so `onSaved` cannot be missed by
   *  one of the two paths. */
  async function commit(): Promise<boolean> {
    const needsRestart = await s.commit()
    onSaved?.()
    return needsRestart
  }

  async function saveChanges(): Promise<void> {
    const needsRestart = await commit()
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

  const shown = GROUPS.filter((g) => g.tab === tab)

  return (
    /* The two things an `extras` control may do to the page it is rendered in: ask whether
       anything is unsaved, and save it. NOT `saveChanges` — the restart belongs to the page's own
       Save button, and a Test button that silently restarted the daemon would be a surprise
       nobody asked for. */
    <SettingsActionsContext.Provider value={{ dirty: s.dirty, commit }}>
    <div className="settings-page">
      {/* PINNED: the header and the nav never scroll, only the body does. A tab strip that
          scrolls away takes the way out of a long tab with it. */}
      <div className="settings-head">
        <div className="settings-title">Settings</div>
        <div className="settings-sub">
          This agent, the daemon it runs on, and the keys that pay for it.
        </div>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`settings-nav-item ${t.id === tab ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {icons?.[t.id]}
              <span>{t.label}</span>
            </button>
          ))}
        </nav>

        <div className="settings-scroll">
          <div className="settings-inner">
            {extras?.[tab]}
            {shown.map((group) => {
          // Nothing declared -> no group. An empty "What this agent needs" reads as a page that
          // failed to load its fields.
          if (group.declared && !(data.settings || []).length) return null

          return (
            /* agentd's group shape: a small uppercase label, the help under it, and the fields
               inside ONE bordered card. This was an <h2> over hairline-separated rows, which read
               as a different product on a page whose whole purpose is to be the same one. */
            <section className="settings-group" key={group.title}>
              <div className="settings-section">{group.title}</div>
              {group.help && <p className="settings-help">{group.help}</p>}
              <div className="settings-card">

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
                      onClear={group.agent ? () => s.clearOverride(spec) : undefined}
                      pinnedBy={pinned[f.key]}
                      lockedByAuthor={s.lockedByAuthor(spec)}
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
              </div>
            </section>
              )
            })}

            {/* ONE SAVE BAR for the whole page, not one per tab: the draft spans every tab, so a
                change made under Models and another under Runtime go to the daemon together. It
                sits inside the scrolling column and sticks to its bottom, so it is reachable
                without scrolling to the end of a long tab. */}
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
      </div>
    </div>
    </SettingsActionsContext.Provider>
  )
}
