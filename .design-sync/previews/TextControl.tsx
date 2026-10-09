/* TextControl — the commit-on-blur input the settings rows use. Its look comes from the
 * `.settings-page .field input` rule, so it is shown inside a settings row's frame. */
import { TextControl } from 'agent-app'

const noop = () => {}

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="settings-page" style={{ width: 460 }}>
    <div className="settings-card">
      <div className="field">
        <div>
          <label>{label}</label>
        </div>
        {children}
      </div>
    </div>
  </div>
)

export const Text = () => (
  <Row label="Model">
    <TextControl type="text" value="claude-sonnet-5" disabled={false} onCommit={noop} />
  </Row>
)

export const Number = () => (
  <Row label="Max turns per run">
    <TextControl type="number" value="120" disabled={false} onCommit={noop} />
  </Row>
)

export const Disabled = () => (
  <Row label="Workspace">
    <TextControl type="text" value="/srv/agentd/workspace" disabled={true} onCommit={noop} />
  </Row>
)
