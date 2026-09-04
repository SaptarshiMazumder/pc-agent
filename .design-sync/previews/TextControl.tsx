/* TextControl — the commit-on-blur input the settings rows use. */
import { TextControl } from 'agent-app'

const noop = () => {}

export const Text = () => (
  <div style={{ width: 320 }}>
    <TextControl type="text" value="claude-sonnet-5" disabled={false} onCommit={noop} />
  </div>
)

export const Number = () => (
  <div style={{ width: 320 }}>
    <TextControl type="number" value="120" disabled={false} onCommit={noop} />
  </div>
)

export const Disabled = () => (
  <div style={{ width: 320 }}>
    <TextControl type="text" value="locked by the author" disabled={true} onCommit={noop} />
  </div>
)
