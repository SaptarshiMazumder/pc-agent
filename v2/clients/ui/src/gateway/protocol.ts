/**
 * Wire protocol types — the TS mirror of agentd/presentation/protocol.py and the
 * event payloads the daemon broadcasts (see the terminal client for the reference
 * rendering of each event type).
 */

export interface RequestFrame {
  type: 'req'
  id: string
  method: string
  params: Record<string, unknown>
}

export interface ResponseFrame {
  type: 'res'
  id: string
  ok: boolean
  payload: Record<string, any>
}

export interface EventFrame {
  type: 'event'
  event: string
  payload: Record<string, any>
}

export type Frame = ResponseFrame | EventFrame

/** An APP AGENT's own UI, served by the daemon at /apps/<id>/ (docs/PROTOCOL.md §9).
 *  Present only when the agent ships a ui/ + [app] in its agent.toml. `url` is
 *  origin-relative and tokenless — the opener appends the live token + scope. */
export interface AgentApp {
  title: string
  url: string
  /** the author's declared presentation: the app's own chromeless window ("program"
   *  feel) or a normal browser tab. Openers honor it; absent = browser. */
  mode?: 'window' | 'browser'
}

export interface AgentInfo {
  id: string
  name: string
  version?: string
  /** server-owned display line, authored in agent.toml or auto-generated once */
  tagline?: string
  /** up to 3 starter prompts for this agent's empty chat (server-owned) */
  suggestions?: string[]
  /** avatar/dot colour (hex) — server-assigned, unique across agents */
  color?: string
  /** the agent's own app UI, when it ships one (null/absent for plain chat agents) */
  app?: AgentApp | null
}

export interface Hello {
  agentName: string
  agentId: string
  model: string
  version: string
  protocol: number
  product: string
  productId: string
  storeEnabled: boolean
  registryConfigured: boolean
  registryUrl: string
  localRegistryDir: string
  workspace: string
  agents: AgentInfo[]
  /** hosted-platform state (absent on older daemons): sign-in endpoint + whether model
   *  calls currently run on platform keys vs the user's own (BYOK) */
  platform?: {
    accountsUrl: string
    modelProxy?: { enabled: boolean; api_base: string; source: string; has_key: boolean }
    /** Deprecated compatibility field returned by older daemons. */
    modelGateway?: { enabled: boolean; api_base: string; source: string; has_key: boolean }
  }
}

export interface SessionRow {
  sessionId: string
  title: string
  titleManual: boolean
  /** preview of the first user message (2nd line in wide chat tables); '' for untitled chats
   *  where the title already IS the first message */
  snippet?: string
  projectId: string
  messages: number
  modified: number
  /** Which agent's partition holds this transcript. Populated by sessions.list (single-agent =
   *  the requested agent; cross-agent Recents/Project view = each row's owning agent). Cross-agent
   *  lists mix agents, so resuming a row must switch currentAgentId to this value. */
  agentId: string
}

export interface ProjectRow {
  id: string
  name: string
  /** the project's LEAD agent — answers when you "message the project" ('' => main) */
  defaultAgentId?: string
  /** curated agent roster shown in the project UI (lead may still call any agent) */
  members?: string[]
  createdAt: number
}

/** chat.event payload: {sessionKey, runId, event: {type, ...}} */
export interface AgentEvent {
  type: string
  [key: string]: any
}

/** A standalone INSTALLER for one platform — what someone with no agentd yet downloads.
 *  Distinct from installing the bundle, which needs a daemon already running. `url` arrives
 *  ABSOLUTE (the daemon joins it against the registry base), so it can be linked as-is. */
export interface InstallerAsset {
  platform: string // 'win' | 'mac' | 'linux'
  url: string
  size: number
  sha256: string
}

export interface CatalogBundle {
  id: string
  name: string
  version: string
  description: string
  price: string
  size: number
  compatible: boolean
  installed: boolean
  installedVersion: string
  updateAvailable: boolean
  entitlement: string
  /** glyph name declared in the bundle manifest ('' => client default glyph) */
  icon?: string
  /** absent when the publisher shipped no installer for this bundle */
  installers?: InstallerAsset[]
  /** which doors the author opened (older daemons omit it: exe on, web off) */
  delivery?: { web?: boolean; exe?: boolean }
  /** finished Open-in-browser link — present only when the author declared web delivery AND
   *  the registry names a hosted deployment. The daemon joins the two; never build this here. */
  webUrl?: string
}

export interface InstalledBundle {
  id: string
  version: string
  installedAt: string
  pluginIds: string[]
}

/** capabilities.list — the ONE uniform shape for everything the runtime exposes (tools, plugins,
 *  skills, agents). Core resolves every description once and serves it to every client equally. */
export interface CapabilityDescriptor {
  kind: 'tool' | 'plugin' | 'skill' | 'agent'
  id: string
  name: string
  description: string
  /** where it lives (a path or owning-plugin id) — lets a client open/inspect it */
  source: string
  extra: Record<string, unknown>
}

/** The full text of a tool result (a message dict with content blocks). */
export function resultText(result: any): string {
  if (result && typeof result === 'object') {
    const content = result.content
    if (Array.isArray(content)) {
      return content
        .map((block: any) => (block && typeof block === 'object' ? block.text || '' : ''))
        .join('')
        .trim()
    }
    return String(result.text || '').trim()
  }
  return String(result ?? '').trim()
}
