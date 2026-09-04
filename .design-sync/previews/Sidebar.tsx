/* Sidebar — the window's rail. The account object is the real Auth shape with a signed-out
 * state (auth: null renders the sign-in affordance — an honest static state; a signed-in one
 * needs a live accounts service). */
import { Sidebar } from 'agent-app'
import { Workflow } from 'lucide-react'

const noop = () => {}
const account = {
  auth: null,
  busy: false,
  error: '',
  signIn: noop,
  wantsSignIn: false,
  signedIn: noop,
  signOut: async () => {},
}

export const Rail = () => (
  <div style={{ height: 620, display: 'flex' }}>
    <Sidebar
      view="chat"
      onView={noop}
      onNewChat={noop}
      account={account}
      status="open"
      name="Comfy Artchitect"
      counts={{ credits: '12,400' }}
      extraDestinations={[{ id: 'workflows', label: 'Workflows', icon: <Workflow size={15} /> }]}
    />
  </div>
)

export const OnWorkflowsView = () => (
  <div style={{ height: 620, display: 'flex' }}>
    <Sidebar
      view="workflows"
      onView={noop}
      onNewChat={noop}
      account={account}
      status="open"
      name="Comfy Artchitect"
      extraDestinations={[{ id: 'workflows', label: 'Workflows', icon: <Workflow size={15} /> }]}
    />
  </div>
)
