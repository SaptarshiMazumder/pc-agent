/* Field — one settings row: label + help on the left, the control on the right. The variant
 * axis is the control type.
 *
 * The row's styles are scoped to `.settings-page` (they would leak into every window's `.field`
 * otherwise), so a row renders as the app renders it only inside that page, in the group's
 * bordered card. */
import { Field } from 'agent-app'

const noop = () => {}

const Card = ({ children }: { children: React.ReactNode }) => (
  <div className="settings-page" style={{ width: 600 }}>
    <div className="settings-card">{children}</div>
  </div>
)

export const Text = () => (
  <Card>
    <Field
      spec={{ key: 'model', label: 'Model', type: 'text', help: 'The model that runs ordinary turns.' }}
      value="claude-sonnet-5"
      onChange={noop}
    />
  </Card>
)

export const Toggle = () => (
  <Card>
    <Field
      spec={{ key: 'verify_tool', label: 'Verify answers', type: 'toggle', help: 'Catches "I made a workflow" when it only described one.' }}
      value={true}
      onChange={noop}
    />
  </Card>
)

export const Select = () => (
  <Card>
    <Field
      spec={{
        key: 'reasoning_effort',
        label: 'Reasoning effort',
        type: 'select',
        help: 'How long the model thinks before answering.',
        options: ['low', 'medium', 'high'],
      }}
      value="medium"
      onChange={noop}
    />
  </Card>
)

export const OverriddenByThisAgent = () => (
  <Card>
    <Field
      spec={{ key: 'model', label: 'Model', type: 'text', help: 'The model that runs ordinary turns.' }}
      value="claude-opus-5"
      source="this agent"
      onClear={noop}
      onChange={noop}
    />
  </Card>
)

export const PinnedByEnv = () => (
  <Card>
    <Field
      spec={{ key: 'workspace', label: 'Workspace', type: 'text' }}
      value="/srv/agentd/workspace"
      pinnedBy="AGENTD_WORKSPACE"
      onChange={noop}
    />
  </Card>
)
