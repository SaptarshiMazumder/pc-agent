/* Agent Builder's settings window.
 *
 * THE FIELDS ARE NOT WRITTEN HERE ANY MORE. They come from the common settings module — the same
 * page every agent this builder produces will ship, imported rather than copied because
 * `_common/` and this app travel together in the same install.
 *
 * That is the point of doing it this way round: Agent Builder runs the module it hands out, so a
 * page that is broken for agents is broken here first, where somebody notices. It used to keep its
 * own schema of 31 knobs beside the assistant's 43, with different groupings and different names
 * for the same things, and nothing compared them.
 *
 * WHAT STAYS HERE is what is not part of that page: run mode and connected services are properties
 * of this MACHINE rather than of a config layer, and the restart button is a lifecycle control.
 */

import type { AgentdClient } from '@agentd/client'
import { Settings } from '../../../../skills/build-agent/templates/_common/settings/Settings'
import { AGENT_ID } from '../../agentd/client'
import { usePlatform, useRestartDaemon } from '../../agentd/platform'
import { useServices } from '../../agentd/services'
import { ModeSection } from './ModeSection'
import { RestartSection } from './RestartSection'
import { ServicesSection } from './ServicesSection'

export function SettingsView({ client }: { client: AgentdClient }) {
  const platform = usePlatform(client)
  const services = useServices(client)
  const daemon = useRestartDaemon(client)

  return (
    <div className="settings-scroll">
      <div className="settings-inner">
        {/* SIGNING IN MOVED OUT, to the account menu at the bottom of the sidebar. It is the first
            thing a new user needs and it was three scrolls into a config screen; it also belongs
            next to the identity it changes, not next to reasoning effort. Run mode stays because
            it is a property of the MACHINE — it applies to every agent on it. */}
        <ModeSection
          auth={platform.auth}
          chosen={platform.chosen}
          error={platform.error}
          onSwitch={(next) => void platform.switchMode(next)}
        />
        <ServicesSection
          servers={services.servers}
          connections={services.connections}
          onApprove={services.approve}
          onConnect={services.connect}
          onDisconnect={services.disconnect}
        />

        {/* Last, and after the fields on purpose: it is the thing you reach for once everything
            else is set and the daemon is still serving what it loaded at boot. Save restarts on
            its own when the daemon asks for it — this is the manual door. */}
        <RestartSection
          onRestart={() => void daemon.restart()}
          busy={daemon.busy}
          note={daemon.note}
        />
      </div>

      {/* The shared page. It brings its own save bar, its own scroll container and the two-layer
          rule — this agent's values win over the daemon's, key by key. */}
      <Settings client={client} agentId={AGENT_ID} onRestart={daemon.restart} />
    </div>
  )
}
