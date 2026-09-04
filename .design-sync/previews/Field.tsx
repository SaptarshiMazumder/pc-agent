/* Field — one settings row: label + help on the left, the control on the right. The variant
 * axis is the control type. */
import { Field } from 'agent-app'

const noop = () => {}

export const Text = () => (
  <Field
    spec={{ key: 'model', label: 'Model', type: 'text', help: 'The model that runs ordinary turns.' }}
    value="claude-sonnet-5"
    onChange={noop}
  />
)

export const Toggle = () => (
  <Field
    spec={{ key: 'verify_tool', label: 'Verify answers', type: 'toggle', help: 'Catches "I made a workflow" when it only described one.' }}
    value={true}
    onChange={noop}
  />
)

export const Select = () => (
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
)

export const OverriddenByThisAgent = () => (
  <Field
    spec={{ key: 'model', label: 'Model', type: 'text', help: 'The model that runs ordinary turns.' }}
    value="claude-opus-5"
    source="this agent"
    onClear={noop}
    onChange={noop}
  />
)

export const PinnedByEnv = () => (
  <Field
    spec={{ key: 'workspace', label: 'Workspace', type: 'text' }}
    value="/srv/agentd/workspace"
    pinnedBy="AGENTD_WORKSPACE"
    onChange={noop}
  />
)
