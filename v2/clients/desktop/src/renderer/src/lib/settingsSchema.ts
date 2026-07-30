/**
 * Declarative schema for the Settings page. Each tab groups the daemon's agent_runtime.config
 * knobs (and a few client-only + provider-key fields) into cards, and SettingsView renders
 * every field from this description with one generic control per `type`. Add a knob here —
 * no new component — and it shows up, edits a draft, and saves through config.set.
 *
 * A field `key` addresses its value:
 *   "model"                   -> config.values.model            (scope 'config', top-level)
 *   "cost_efficiency.enabled" -> nested inside a config object   (scope 'config', dotted)
 *   "env:OPENAI_API_KEY"      -> a provider secret in .env        (scope 'env')
 *   "client:language"         -> a renderer-only preference        (scope 'client', localStorage)
 *
 * Dropdown options are DATA, not hardcoded: a field sets `optionsKey` (e.g. 'models') and the
 * options come from config.get's `catalogs[optionsKey]` — the daemon owns the list, so it stays
 * in sync and is extendable from the config file. `showWhen` hides a field until a toggle is on.
 */

import { Sparkles, Cpu, KeyRound, Wrench, Boxes, Server } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type FieldType =
  | 'toggle'
  | 'segment'
  | 'text'
  | 'number'
  | 'select'
  | 'modellist'
  | 'list'
  | 'textarea'
  | 'secret'

export type FieldScope = 'config' | 'env' | 'client'

export interface FieldDef {
  key: string
  label: string
  help?: string
  type: FieldType
  options?: Array<{ value: string; label: string; group?: string }>
  /** pull options from config.get's catalogs[optionsKey] instead of a static list */
  optionsKey?: string
  /** only render this field when another (dotted) key equals a value */
  showWhen?: { key: string; equals: unknown }
  placeholder?: string
  min?: number
  max?: number
  step?: number
  unit?: string
}

export interface GroupDef {
  title: string
  help?: string
  fields: FieldDef[]
}

export interface TabDef {
  id: string
  label: string
  icon: LucideIcon
  /** custom-rendered blocks that precede this tab's groups (or replace them, for runtime) */
  custom?: 'plugins' | 'runtime' | 'tools' | 'models'
  groups: GroupDef[]
}

export function fieldScope(key: string): FieldScope {
  if (key.startsWith('env:')) return 'env'
  if (key.startsWith('client:')) return 'client'
  return 'config'
}

/** The bare key without its scope prefix (for env/client fields). */
export function bareKey(key: string): string {
  const i = key.indexOf(':')
  return i >= 0 ? key.slice(i + 1) : key
}

const REASONING = ['off', 'low', 'medium', 'high'].map((v) => ({ value: v, label: v }))

export const SETTINGS_TABS: TabDef[] = [
  {
    id: 'general',
    label: 'General',
    icon: Sparkles,
    groups: [
      {
        title: 'Appearance',
        fields: [
          {
            key: 'client:theme',
            label: 'Theme',
            help: 'Light or dark shell.',
            type: 'segment',
            options: [
              { value: 'light', label: 'Light' },
              { value: 'dark', label: 'Dark' }
            ]
          }
        ]
      },
      {
        title: 'Assistant',
        fields: [
          {
            key: 'agent_name',
            label: 'Assistant name',
            help: 'How the default agent introduces and identifies itself.',
            type: 'text',
            placeholder: 'JARVIS'
          },
          {
            key: 'reasoning_effort',
            label: 'Reasoning effort',
            help: 'Depth of the model’s thinking pass.',
            type: 'segment',
            options: REASONING
          },
          {
            key: 'completeness_check',
            label: 'Honesty & completeness check',
            help: 'Back claims with real evidence, never fabricate.',
            type: 'toggle'
          }
        ]
      },
      {
        title: 'Notifications',
        fields: [
          {
            key: 'client:notifications',
            label: 'Desktop notifications',
            help: 'Ping me when a long run finishes.',
            type: 'toggle'
          },
          {
            key: 'notify_enabled',
            label: 'Outbound notifications',
            help: 'Let the daemon reach you when a scheduled run ends blocked or failed.',
            type: 'toggle'
          }
        ]
      }
    ]
  },
  {
    id: 'models',
    label: 'Models',
    icon: Cpu,
    // custom panel (ModelsPanel): the fields shown depend on cost efficiency, so what you see
    // is always the model(s) that actually run — never a bypassed field shown as if it's active.
    custom: 'models',
    groups: []
  },
  {
    id: 'keys',
    label: 'API Keys',
    icon: KeyRound,
    groups: [
      {
        title: 'Provider keys',
        help: 'Stored in your local .env and applied live — the model layer reads them straight from the environment. Leave a field blank to keep the current value.',
        fields: [
          { key: 'env:ANTHROPIC_API_KEY', label: 'Anthropic', type: 'secret', placeholder: 'sk-ant-…' },
          { key: 'env:OPENAI_API_KEY', label: 'OpenAI', type: 'secret', placeholder: 'sk-…' },
          { key: 'env:GEMINI_API_KEY', label: 'Google Gemini', type: 'secret', placeholder: 'AIza…' },
          { key: 'env:OPENROUTER_API_KEY', label: 'OpenRouter', type: 'secret' },
          { key: 'env:DEEPSEEK_API_KEY', label: 'DeepSeek', type: 'secret' },
          { key: 'env:GROQ_API_KEY', label: 'Groq', type: 'secret' },
          { key: 'env:XAI_API_KEY', label: 'xAI (Grok)', type: 'secret' },
          { key: 'env:MISTRAL_API_KEY', label: 'Mistral', type: 'secret' }
        ]
      },
      {
        title: 'Media & search keys',
        help: 'Optional keys for image generation and web search backends.',
        fields: [
          { key: 'env:FAL_KEY', label: 'fal.ai', type: 'secret' },
          { key: 'env:REPLICATE_API_TOKEN', label: 'Replicate', type: 'secret' },
          { key: 'env:BRAVE_API_KEY', label: 'Brave Search', type: 'secret' },
          { key: 'env:PARALLEL_API_KEY', label: 'Parallel Search', type: 'secret' }
        ]
      }
    ]
  },
  {
    id: 'tools',
    label: 'Tools & plugins',
    icon: Wrench,
    custom: 'tools',
    groups: []
  },
  {
    id: 'capabilities',
    label: 'Capabilities',
    icon: Boxes,
    groups: [
      {
        title: 'Sub-agents & messaging',
        fields: [
          { key: 'subagents_enabled', label: 'Sub-agents', help: 'Let the agent delegate a subtask to a fresh child run.', type: 'toggle' },
          { key: 'subagent_max', label: 'Max concurrent children', type: 'number', min: 1, max: 32, showWhen: { key: 'subagents_enabled', equals: true } },
          { key: 'subagent_max_depth', label: 'Max nesting depth', help: '1 = no nesting; hard ceiling 5.', type: 'number', min: 1, max: 5, showWhen: { key: 'subagents_enabled', equals: true } },
          { key: 'agent_messaging_enabled', label: 'Agent-to-agent messaging', help: 'Let an agent message another persistent agent and get a reply.', type: 'toggle' }
        ]
      },
      {
        title: 'Memory',
        fields: [
          { key: 'memory_enabled', label: 'Long-term memory', help: 'Durable memory the agent recalls across sessions.', type: 'toggle' },
          { key: 'memory_auto_recall', label: 'Auto-recall', help: 'Silently retrieve relevant memories on each user turn.', type: 'toggle', showWhen: { key: 'memory_enabled', equals: true } },
          { key: 'memory_auto_recall_limit', label: 'Auto-recall limit', help: 'How many memories to prepend.', type: 'number', min: 1, max: 50, showWhen: { key: 'memory_enabled', equals: true } }
        ]
      },
      {
        title: 'Autonomy',
        fields: [
          { key: 'autonomy_enabled', label: 'Heartbeat autonomy', help: 'Wake agents that declare a heartbeat interval to act on a tick.', type: 'toggle' },
          { key: 'heartbeat_default_interval', label: 'Default interval', help: 'e.g. 30m. Per-agent agent.toml overrides.', type: 'text', placeholder: '30m', showWhen: { key: 'autonomy_enabled', equals: true } },
          { key: 'heartbeat_active_hours', label: 'Active hours', help: 'e.g. 08:00-22:00 (blank = always).', type: 'text', placeholder: '08:00-22:00', showWhen: { key: 'autonomy_enabled', equals: true } }
        ]
      },
      {
        title: 'Self-extension (workshops)',
        help: 'Let the agent extend itself by chatting. Each writes to disk; enable only what you trust.',
        fields: [
          { key: 'skill_workshop', label: 'Skill workshop', help: 'Author reusable SKILL.md playbooks at runtime.', type: 'toggle' },
          { key: 'agent_workshop', label: 'Agent workshop', help: 'Author new agent definitions and register them live.', type: 'toggle' },
          { key: 'mcp_workshop', label: 'MCP workshop', help: 'Connect an MCP server by chatting.', type: 'toggle' },
          { key: 'tool_workshop', label: 'Tool workshop', help: 'Author and hot-load new Python tools in-process (RCE by design).', type: 'toggle' }
        ]
      },
      {
        title: 'Optional tools',
        fields: [
          { key: 'verify_tool', label: 'Self-verify tool', help: 'Register verify_answer so the agent can review its own draft before replying.', type: 'toggle' },
          { key: 'computer_enabled', label: 'Computer use', help: 'Let the agent drive the real mouse/keyboard/screen. Sends full-screen screenshots to the model.', type: 'toggle' },
          { key: 'parallel_search_enabled', label: 'Parallel web search', help: 'Keyless hosted search backend (no API key required).', type: 'toggle' }
        ]
      },
      {
        title: 'Safety & workspace',
        fields: [
          { key: 'safe_to_send_check', label: 'Safe-to-send gate', help: 'Judge outbound replies on public channels before they leave.', type: 'toggle' },
          { key: 'workspace_index_enabled', label: 'Workspace index', help: 'Inject a manifest of workspace files into every prompt.', type: 'toggle' },
          { key: 'resource_manager_enabled', label: 'Resource manager', help: 'A described index of workspace resources + a resource tool.', type: 'toggle' },
          { key: 'resource_vision_enabled', label: 'Image captions', help: 'Vision captions for image resources (sends image bytes to the model).', type: 'toggle' },
          { key: 'resource_summarize_enabled', label: 'Text summaries', help: 'One-line summaries for text resources (sends content to the model).', type: 'toggle' },
          { key: 'event_log_enabled', label: 'Durable event log', help: 'Record every run’s event stream for later inspection.', type: 'toggle' }
        ]
      }
    ]
  },
  {
    id: 'runtime',
    label: 'Runtime',
    icon: Server,
    custom: 'runtime',
    groups: [
      {
        title: 'Server',
        help: 'The gateway bind address. Changing these needs a daemon restart.',
        fields: [
          { key: 'host', label: 'Host', type: 'text', placeholder: '127.0.0.1' },
          { key: 'port', label: 'Port', type: 'number', min: 1, max: 65535 },
          { key: 'workspace', label: 'Workspace', help: 'Where file/exec tools operate.', type: 'text' },
          { key: 'public_url', label: 'Public URL', help: 'Base URL the gateway is reachable at (for webhook/connect links).', type: 'text', placeholder: 'https://…' }
        ]
      },
      {
        title: 'Limits',
        help: 'Advanced run/model limits — the defaults are fine for most setups.',
        fields: [
          { key: 'max_turns', label: 'Max turns per run', help: 'Model turns per run before the loop stops.', type: 'number', min: 1, max: 1000 },
          { key: 'llm_idle_timeout_seconds', label: 'Model idle timeout', help: 'Abort a model stream silent this long.', type: 'number', min: 5, unit: 's' },
          { key: 'llm_request_timeout_seconds', label: 'Model request timeout', help: 'Hard ceiling per model call.', type: 'number', min: 10, unit: 's' },
          { key: 'tool_timeout_default', label: 'Tool timeout', help: 'Wall-clock per tool call.', type: 'number', min: 1, unit: 's' },
          { key: 'tool_retries_default', label: 'Tool retries', help: 'Extra attempts on transient errors.', type: 'number', min: 0, max: 10 }
        ]
      }
    ]
  }
]
