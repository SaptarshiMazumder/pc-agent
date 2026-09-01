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
import { Boxes, Cpu, KeyRound, Server, Sparkles, Wrench } from 'lucide-react'

import { Settings } from '../../../../skills/build-agent/templates/_common/settings/Settings'
import { AGENT_ID } from '../../agentd/client'
import { usePlatform, useRestartDaemon } from '../../agentd/platform'
import { useServices } from '../../agentd/services'
import { ModeSection } from './ModeSection'
import { RestartSection } from './RestartSection'
import { ServicesSection } from './ServicesSection'

/* The six tab icons. Passed IN rather than imported by the shared module: a scaffolded agent's
   package.json has react and react-dom and nothing else, so the module cannot depend on an icon
   set without adding one to every agent ever built. This window already has lucide. */
const ICONS = {
  general: <Sparkles size={16} />,
  models: <Cpu size={16} />,
  keys: <KeyRound size={16} />,
  tools: <Wrench size={16} />,
  capabilities: <Boxes size={16} />,
  runtime: <Server size={16} />,
}

export function SettingsView({ client }: { client: AgentdClient }) {
  const platform = usePlatform(client)
  const services = useServices(client)
  const daemon = useRestartDaemon(client)

  return (
    /* ONE PAGE. These three used to sit in a column ABOVE the shared settings page, so the window
       had two stacked settings surfaces with two scrollbars and no relationship between them.
       They are slotted into the tab each one belongs to instead:

         Run mode   -> General, because it is the first thing about how this machine runs
         Services   -> Tools & plugins, beside the tools they provide
         Restart    -> Runtime, with the other daemon-lifecycle controls

       Everything else on the page comes from the shared schema, which is why they arrive as
       `extras` rather than as more groups: the schema describes CONFIG, and none of these three
       is a config key. */
    <Settings
      client={client}
      agentId={AGENT_ID}
      onRestart={daemon.restart}
      icons={ICONS}
      extras={{
        general: (
          /* SIGNING IN IS NOT HERE — it is the account menu at the bottom of the sidebar, next to
             the identity it changes. Run mode stays because it is a property of the MACHINE: it
             applies to every agent on it, not to this one. */
          <ModeSection
            auth={platform.auth}
            chosen={platform.chosen}
            error={platform.error}
            onSwitch={(next) => void platform.switchMode(next)}
          />
        ),
        tools: (
          <ServicesSection
            servers={services.servers}
            connections={services.connections}
            onConnect={services.connect}
            onDisconnect={services.disconnect}
          />
        ),
        runtime: (
          /* The manual door. Save restarts on its own when the daemon says its running copy is
             stale; this is for the times you know it is and it does not. */
          <RestartSection
            onRestart={() => void daemon.restart()}
            busy={daemon.busy}
            note={daemon.note}
          />
        ),
      }}
    />
  )
}
