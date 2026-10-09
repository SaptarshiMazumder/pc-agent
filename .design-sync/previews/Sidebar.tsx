/* Sidebar — the window's rail. The account object is the real Auth shape with a signed-out
 * state (auth: null renders the sign-in affordance — an honest static state; a signed-in one
 * needs a live accounts service).
 *
 * The Recent list reads the app store (`useApp().chats`), which App fills from the daemon's
 * session list. The store ships in the bundle, so the preview seeds it exactly the way
 * `setChats` would after that read — otherwise the rail sits on "Loading conversations…". */
import { Sidebar, useApp } from 'agent-app'
import { Workflow } from 'lucide-react'

const now = Math.floor(Date.now() / 1000)
useApp.setState({
  chatsLoading: false,
  chatsError: '',
  currentSessionKey: 's1',
  chats: [
    { sessionId: 's1', title: 'Rooftop portrait at golden hour, 1024×1536', messages: 14, modified: now - 300, running: true },
    { sessionId: 's2', title: 'Product ad — ceramic mug on linen', messages: 22, modified: now - 7200 },
    { sessionId: 's3', title: 'Lip-sync test with the singing clip', messages: 9, modified: now - 86400 * 2 },
    { sessionId: 's4', title: 'Anime key visual, rainy neon street', messages: 6, modified: now - 86400 * 4 },
    { sessionId: 's5', title: 'Flux portrait with a face reference', messages: 18, modified: now - 86400 * 9 },
  ],
})

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
      onRenameChat={async () => {}}
      onDeleteChat={async () => {}}
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

export const Disconnected = () => (
  <div style={{ height: 620, display: 'flex' }}>
    <Sidebar
      view="chat"
      onView={noop}
      onNewChat={noop}
      account={account}
      status="reconnecting"
      name="Comfy Artchitect"
      extraDestinations={[{ id: 'workflows', label: 'Workflows', icon: <Workflow size={15} /> }]}
    />
  </div>
)
