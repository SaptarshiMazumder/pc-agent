/* WHAT THE SETTINGS PAGE SHOWS, and in what order.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. A knob every agent should expose belongs HERE, so every agent gets it at once.
 *
 * A TABLE RATHER THAN MARKUP. Every row is the same control with a different label, and the two
 * layers — this agent, and the daemon — are the same fields rendered against a different path.
 * Adding a knob is a row; it needs no component.
 *
 * IT MIRRORS THE AGENTD SETTINGS WINDOW ON PURPOSE. A user who configures the assistant and then
 * opens an agent should not meet a different, smaller page with different names for the same
 * things. `tests/unit/test_settings_parity.py` compares this list against
 * `clients/ui/src/lib/settingsSchema.ts` and fails when they drift, because "the same page" is a
 * promise that only survives if something checks it.
 *
 * WHICH LAYER A FIELD BELONGS TO:
 *
 *   agent: true    the agent may decide it for itself; its value wins over the daemon's
 *   machine: true  a property of the MACHINE (ports, paths, diagnostics). Shown read-only in an
 *                  agent's window, because an agent offering to move the daemon's workspace is
 *                  offering to break the install.
 */

export type FieldType = 'text' | 'number' | 'toggle' | 'select'

/** One entry in a daemon-owned option list. Every spelling the daemon has used is accepted —
 *  a settings page that renders a blank dropdown because a list said `id` instead of `value` is
 *  a page that looks broken for a reason nobody can see. */
export interface CatalogOption {
  value?: string
  id?: string
  label?: string
  name?: string
}

export interface FieldSpec {
  key: string
  label: string
  type: FieldType
  help?: string
  /** Static choices. Use `catalog` instead when the daemon owns the list. */
  options?: string[]
  /** Pull choices from config.get's `catalogs[...]` — the daemon owns models, so they stay in
   *  sync and a new one needs no client release. */
  catalog?: string
  /** Resolved against this agent's own layer rather than the daemon's. */
  agent?: boolean
  /** Machine-wide: rendered, but never editable from inside an agent's window. */
  machine?: boolean
}

export interface Group {
  title: string
  help?: string
  /** Do this group's fields belong to the agent layer? */
  agent?: boolean
  /** Render the declared [[settings]] the daemon sent, instead of `fields`. */
  declared?: boolean
  /** Render the provider keys, instead of `fields`. */
  secrets?: boolean
  /** Append the cost-efficiency rows after `fields`. */
  costEfficiency?: boolean
  /** Machine-wide group: read-only inside an agent's window. */
  machine?: boolean
  fields?: FieldSpec[]
}

const EFFORTS = ['minimal', 'low', 'medium', 'high']

/** The knobs an agent decides for itself. Both layers render this same list — the agent group
 *  against its own block, the daemon group against the daemon's. */
const BRAIN: FieldSpec[] = [
  { key: 'model', label: 'Model', type: 'select', catalog: 'models', help: 'The brain for this agent’s runs.' },
  { key: 'reasoning_effort', label: 'Reasoning effort', type: 'select', options: EFFORTS },
  {
    key: 'max_turns',
    label: 'Max turns per run',
    type: 'number',
    help: 'Tool-call rounds before the run stops on its own.',
  },
  { key: 'verify_tool', label: 'Self-verify', type: 'toggle', help: 'Review its own draft before replying.' },
  { key: 'memory_enabled', label: 'Long-term memory', type: 'toggle' },
]

export const GROUPS: Group[] = [
  {
    title: 'This agent',
    help:
      'Settings for this agent alone. They decide how it runs — they win over the daemon-wide ' +
      'values below, one knob at a time. Anything left unset here keeps using the daemon’s.',
    agent: true,
    fields: BRAIN,
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
      'Your own provider keys. Stored in the .env beside the config, never in the config file ' +
      'itself, and read straight from the environment by the model layer. Shared by every agent ' +
      'on this machine — keys are not per-agent.',
    secrets: true,
  },
  {
    title: 'Daemon defaults',
    help: 'What every agent uses unless it sets its own. The same surface the assistant’s settings window edits.',
    fields: [{ key: 'agent_name', label: 'Assistant name', type: 'text' }, ...BRAIN],
    costEfficiency: true,
  },
  {
    title: 'Behaviour',
    fields: [
      { key: 'completeness_check', label: 'Completeness check', type: 'toggle' },
      { key: 'execution_contract', label: 'Execution contract', type: 'toggle' },
      { key: 'safe_to_send_check', label: 'Safe-to-send check', type: 'toggle' },
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
      { key: 'memory_auto_recall_limit', label: 'Auto-recall limit', type: 'number' },
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
      { key: 'parallel_search_enabled', label: 'Parallel search', type: 'toggle' },
      { key: 'resource_manager_enabled', label: 'Resource manager', type: 'toggle' },
      { key: 'resource_vision_enabled', label: 'Resource vision', type: 'toggle' },
      { key: 'resource_summarize_enabled', label: 'Resource summarise', type: 'toggle' },
      { key: 'tool_timeout_default', label: 'Default timeout (s)', type: 'number' },
      { key: 'tool_retries_default', label: 'Default retries', type: 'number' },
    ],
  },
  {
    title: 'Diagnostics',
    machine: true,
    fields: [
      { key: 'event_log_enabled', label: 'Event log', type: 'toggle', machine: true },
      { key: 'diagnostics_upload', label: 'Upload diagnostics', type: 'toggle', machine: true },
    ],
  },
  {
    title: 'Server',
    help: 'Where this daemon listens. Changing these takes effect on restart.',
    machine: true,
    fields: [
      { key: 'host', label: 'Host', type: 'text', machine: true },
      { key: 'port', label: 'Port', type: 'number', machine: true },
      { key: 'public_url', label: 'Public URL', type: 'text', machine: true },
    ],
  },
  {
    title: 'Limits',
    machine: true,
    fields: [
      { key: 'llm_idle_timeout_seconds', label: 'Model idle timeout (s)', type: 'number', machine: true },
      { key: 'llm_request_timeout_seconds', label: 'Model request timeout (s)', type: 'number', machine: true },
    ],
  },
  {
    title: 'Paths',
    help: 'Where this daemon keeps things. Changing these takes effect on restart.',
    machine: true,
    fields: [
      { key: 'workspace', label: 'Workspace', type: 'text', machine: true },
      { key: 'state_dir', label: 'State directory', type: 'text', machine: true },
      { key: 'agents_dir', label: 'Agents directory', type: 'text', machine: true },
    ],
  },
]

/** Cost efficiency, and the Model row it overrules.
 *
 *  When it is ON, `model` is NOT what runs — the router picks per turn. Showing a Model dropdown
 *  beside it would show a value with no effect, which is exactly the confusion this page exists to
 *  end. So: the toggle, and the two brains that replace it.
 *
 *  It is also the knob that made per-agent settings look broken for a whole afternoon: it
 *  overwrites the model every turn, so an agent that named its model watched the daemon's cheap
 *  one answer instead. Hence the Model row disappears rather than lying. */
export const COST_EFFICIENCY_TOGGLE: FieldSpec = {
  key: 'cost_efficiency.enabled',
  label: 'Cost efficiency',
  type: 'toggle',
  help:
    'Run a cheap model on ordinary turns and only switch to a stronger one when the turn ' +
    'actually involves an image.',
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

/** Every config key this page can reach, for the parity test and for a caller that wants to know
 *  whether a knob is covered without walking the groups itself. */
export function allKeys(): string[] {
  const keys = new Set<string>()
  for (const group of GROUPS) {
    for (const field of group.fields || []) keys.add(field.key)
    if (group.costEfficiency) {
      keys.add(COST_EFFICIENCY_TOGGLE.key)
      for (const field of COST_EFFICIENCY_MODELS) keys.add(field.key)
    }
  }
  return [...keys].sort()
}
