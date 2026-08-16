/* What the settings page shows, and in what order.
 *
 * A table rather than markup: every row is the same control with a different label, and the two
 * layers (this agent / the daemon) are the same fields rendered against a different path. Keeping
 * that as data is what stops the two copies from drifting apart.
 */

import { AGENT_ID } from '../../agentd/client'
import type { FieldSpec } from '../../agentd/settings'

export interface Group {
  title: string
  help?: string
  /** Show the "Override JARVIS settings" switch at the top of this group. */
  agentToggle?: boolean
  /** Do this group's fields belong to the agent layer? */
  agent?: boolean
  /** Render the declared [[settings]] the daemon sent, instead of `fields`. */
  declared?: boolean
  /** Render the provider keys, instead of `fields`. */
  secrets?: boolean
  /** Append the cost-efficiency rows after `fields`. */
  costEfficiency?: boolean
  fields?: FieldSpec[]
}

export const GROUPS: Group[] = [
  {
    title: 'This agent',
    help:
      `Settings for ${AGENT_ID} alone. With the override on, these win over the ` +
      'daemon-wide values below — one knob at a time: anything left unset here keeps ' +
      "using the daemon's. Turning the override off does not erase them.",
    agentToggle: true,
    agent: true,
    fields: [
      {
        key: 'model',
        label: 'Model',
        type: 'select',
        catalog: 'models',
        help: "The brain for this agent's runs.",
      },
      {
        key: 'reasoning_effort',
        label: 'Reasoning effort',
        type: 'select',
        options: ['minimal', 'low', 'medium', 'high'],
      },
      {
        key: 'max_turns',
        label: 'Max turns per run',
        type: 'number',
        help: 'Tool-call rounds before the run stops on its own.',
      },
      {
        key: 'verify_tool',
        label: 'Self-verify',
        type: 'toggle',
        help: 'Review its own draft before replying.',
      },
      { key: 'memory_enabled', label: 'Long-term memory', type: 'toggle' },
    ],
    costEfficiency: true,
  },
  {
    // What agent.toml's [[settings]] declared. The daemon sends the FIELDS; the values stay on
    // this machine. An agent that declares nothing shows nothing — the group hides itself.
    title: 'What this agent needs',
    help:
      'Set by whoever runs this agent. Saved to the .env on this machine — never packaged, ' +
      'never sent anywhere with the agent.',
    declared: true,
  },
  {
    title: 'API keys',
    help:
      'Your own provider keys. Stored in the .env beside the config, never in the ' +
      'config file itself, and read straight from the environment by the model layer. ' +
      'Shared by every agent on this machine — keys are not per-agent.',
    secrets: true,
  },
  {
    title: 'Daemon defaults',
    help:
      'What every agent uses unless it overrides it. This is the same surface the ' +
      'JARVIS settings window edits.',
    fields: [
      { key: 'agent_name', label: 'Assistant name', type: 'text' },
      { key: 'model', label: 'Model', type: 'select', catalog: 'models' },
      {
        key: 'reasoning_effort',
        label: 'Reasoning effort',
        type: 'select',
        options: ['minimal', 'low', 'medium', 'high'],
      },
      { key: 'max_turns', label: 'Max turns per run', type: 'number' },
    ],
    costEfficiency: true,
  },
  {
    title: 'Behaviour',
    fields: [
      { key: 'completeness_check', label: 'Completeness check', type: 'toggle' },
      { key: 'execution_contract', label: 'Execution contract', type: 'toggle' },
      {
        key: 'skill_workshop',
        label: 'Skill workshop',
        type: 'toggle',
        help: 'Agents may author reusable SKILL.md playbooks at runtime.',
      },
      { key: 'mcp_workshop', label: 'MCP workshop', type: 'toggle' },
    ],
  },
  {
    title: 'Memory & context',
    fields: [
      { key: 'memory_auto_recall', label: 'Auto-recall', type: 'toggle' },
      { key: 'context_max_messages', label: 'Context window (messages)', type: 'number' },
      { key: 'workspace_index_enabled', label: 'Workspace index', type: 'toggle' },
    ],
  },
  {
    title: 'Delegation',
    fields: [
      { key: 'subagents_enabled', label: 'Sub-agents', type: 'toggle' },
      { key: 'subagent_max', label: 'Max concurrent', type: 'number' },
      { key: 'subagent_max_depth', label: 'Max depth', type: 'number' },
      { key: 'agent_messaging_enabled', label: 'Agent-to-agent messaging', type: 'toggle' },
    ],
  },
  {
    title: 'Autonomy',
    help: 'Scheduled and self-woken runs.',
    fields: [
      { key: 'autonomy_enabled', label: 'Autonomy', type: 'toggle' },
      { key: 'heartbeat_default_interval', label: 'Heartbeat interval', type: 'text' },
      { key: 'heartbeat_active_hours', label: 'Active hours', type: 'text' },
      { key: 'notify_enabled', label: 'Notifications', type: 'toggle' },
    ],
  },
  {
    title: 'Tools',
    fields: [
      { key: 'computer_enabled', label: 'Computer use', type: 'toggle' },
      { key: 'tool_timeout_default', label: 'Default timeout (s)', type: 'number' },
      { key: 'tool_retries_default', label: 'Default retries', type: 'number' },
      { key: 'parallel_search_enabled', label: 'Parallel search', type: 'toggle' },
    ],
  },
  {
    title: 'Paths',
    help: 'Where this daemon keeps things. Changing these takes effect on restart.',
    fields: [
      { key: 'workspace', label: 'Workspace', type: 'text' },
      { key: 'state_dir', label: 'State directory', type: 'text' },
      { key: 'agents_dir', label: 'Agents directory', type: 'text' },
    ],
  },
]

/** Cost efficiency, and the Model row it overrules.
 *
 *  When it is ON the daemon's `model` is NOT what runs — the router picks per turn. Showing a
 *  Model dropdown beside it would be showing a value that has no effect, which is exactly the
 *  confusion this page exists to end. So: the toggle, and the two brains that replace it. */
export const COST_EFFICIENCY_TOGGLE: FieldSpec = {
  key: 'cost_efficiency.enabled',
  label: 'Cost efficiency',
  type: 'toggle',
  help:
    'Run a cheap model on ordinary turns and only switch to a stronger one when the ' +
    'turn actually involves an image.',
}

export const COST_EFFICIENCY_MODELS: FieldSpec[] = [
  {
    key: 'cost_efficiency.text_model',
    label: 'Text model',
    type: 'select',
    catalog: 'models',
    help: 'Ordinary turns — the one you talk to most.',
  },
  {
    key: 'cost_efficiency.vision_model',
    label: 'Vision model',
    type: 'select',
    catalog: 'models',
    help: 'Turns carrying an image, and every turn after one enters the chat.',
  },
]
