/* StudioTopBar — the studio's one strip of chrome: the Workspace / Library tabs, where the work
 * runs ("Comfy Cloud", a plain label rather than a status) and the credit balance.
 *
 * Cells sweep the tab axis (Workspace on, Library on), the attention state the dashboard sets when
 * an input slot is waiting on the person, and the balance before the accounts service answers
 * ("—"). The Library tab's post-save flash is a 1.2 s pulse after a save — a transient, not
 * reachable statically, so it is not shown. */
import { StudioTopBar } from 'agent-app'

const noop = () => {}
const Bar = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 720 }}>{children}</div>

export const OnWorkspace = () => (
  <Bar>
    <StudioTopBar credits={12400} onCredits={noop} panel="workspace" onPanel={noop} />
  </Bar>
)

export const OnLibrary = () => (
  <Bar>
    <StudioTopBar credits={8735} onCredits={noop} panel="library" onPanel={noop} />
  </Bar>
)

export const WaitingOnAnInput = () => (
  <Bar>
    <StudioTopBar credits={12400} onCredits={noop} panel="library" onPanel={noop} attention />
  </Bar>
)

export const BalanceNotLoaded = () => (
  <Bar>
    <StudioTopBar credits={null} onCredits={noop} panel="workspace" onPanel={noop} />
  </Bar>
)
