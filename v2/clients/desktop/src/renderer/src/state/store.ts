/**
 * App state (zustand) — the renderer's single source of truth.
 *
 * bootstrap(): flavor -> ensure daemon -> connect WS -> hello -> sessions/projects ->
 * (Studio flavors) preinstall bundled .agentpkg files. Every broadcast event the
 * daemon emits lands here and mutates exactly one slice; components just render.
 * All conversation data (titles, projects, timestamps, deletion) is SERVER data —
 * this store only mirrors it and renders optimistically where that helps.
 */

import { create } from 'zustand'

import { gateway } from '../gateway/client'
import type {
  AgentEvent,
  AgentInfo,
  CatalogBundle,
  Hello,
  InstalledBundle,
  ProjectRow,
  SessionRow
} from '../gateway/protocol'
import { resultText } from '../gateway/protocol'
import type { Artifact, ArtifactAction } from '../lib/artifacts'
import { setGatewayUrl } from '../lib/artifacts'
import { downloadTextFile, safeFileName, sessionToMarkdown } from '../lib/exportChat'

export type ChatItem = (
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming: boolean }
  | { kind: 'thinking'; text: string; streaming: boolean }
  | { kind: 'tool'; name: string; args: Record<string, unknown>; result: string; isError: boolean; done: boolean; progress?: string }
  | { kind: 'system'; text: string; tone: 'info' | 'error' }
  // a delegated sub-agent run, GROUPED into one drillable block: the child's beats (each tool it
  // ran, one level down) accumulate under `steps` while it runs, then it settles done/error
  | { kind: 'subagent'; agent: string; steps: string[]; status: 'running' | 'done' | 'error'; detail?: string }
) & { ts?: number; artifacts?: Artifact[] } // ts: epoch ms (server-side); artifacts: media the item produced

export interface SessionState {
  items: ChatItem[]
  running: boolean
  // deliverables a tool produced, held until the next assistant answer renders them
  // (so the tool log stays pure text and media collects under the answer)
  pendingArtifacts?: Artifact[]
  // live model/token usage for the CURRENT (or most recent) loop step — surfaced in the
  // persistent status strip under the composer, Claude-style, NOT archived per step in the
  // scrollback. Populated from model_trace events (config.model_trace / AGENTD_MODEL_TRACE);
  // undefined when tracing is off.
  usage?: { model: string; tokensIn: number; tokensOut: number; step: number }
}

/** A file the user is sending TO the agent (e.g. an edited image from the canvas). Bytes
 *  ride the WS send as base64; the daemon saves them to the workspace and hands back real
 *  paths, which then render like any artifact. */
export interface OutgoingAttachment {
  name: string
  mimeType: string
  dataBase64: string
}

export type View =
  | 'chat'
  | 'store'
  | 'settings'
  | 'account'
  | 'subscription'
  | 'datasources'
  | 'projects' // the Projects list page
  | 'project' // one project's detail page (uses currentProjectId)
  | 'agent' // one agent's detail page (uses viewedAgentId)

/** One open chat tab. Tabs OWN their agent binding: sessionRows only ever holds
 *  the CURRENT agent's list, so a tab from another agent must remember where it
 *  lives — for its title, its dot colour, and (critically) for routing messages
 *  to the right agent when it's re-activated. */
export interface OpenTab {
  id: string
  agentId: string
}

export type Theme = 'light' | 'dark'

const THEME_KEY = 'agentd-theme'

/** Read the persisted theme (light is the product default). */
export function initialTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* storage unavailable — theme just won't persist */
  }
}

interface FlavorInfo {
  productId: string
  productName: string
  defaultAgent: string
  storeEnabled: boolean
  preinstalledBundles: string[]
  bundledPackages: string[]
  version: string
}

interface SupervisorStatus {
  phase: 'looking' | 'starting' | 'running' | 'failed'
  message: string
}

interface NotificationRow {
  id: string
  agentId: string
  kind: string
  text: string
  detail: string
  at: string
}

function newSessionKey(): string {
  return `desk-${Math.random().toString(36).slice(2, 10)}`
}

const NOTIFY_PREF_KEY = 'agentd-notifications'

/** Fire an OS notification when a watched run finishes while the window is in the background.
 *  Honors the client "Desktop notifications" pref (localStorage, default on) and never fires
 *  while the app is focused. Best-effort — silently no-ops if notifications are unavailable. */
function desktopNotify(title: string, body: string): void {
  try {
    if (localStorage.getItem(NOTIFY_PREF_KEY) === '0') return // pref off
    if (typeof document !== 'undefined' && document.hasFocus()) return // only when tabbed away
    if (typeof Notification === 'undefined') return
    if (Notification.permission === 'granted') {
      new Notification(title, { body })
    } else if (Notification.permission !== 'denied') {
      void Notification.requestPermission().then((p) => {
        if (p === 'granted') new Notification(title, { body })
      })
    }
  } catch {
    /* notifications unavailable */
  }
}

/** ISO timestamp (as stored in the transcript) -> epoch ms, or undefined. */
function toMs(iso: unknown): number | undefined {
  if (typeof iso !== 'string' || !iso) return undefined
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? undefined : ms
}

/** Rebuild a saved transcript (sessions.history message dicts) into the same
 *  ChatItem[] the live event path produces, so a resumed session renders identically:
 *  user text, assistant text/thinking, and tool calls merged with their results.
 *  Each stored line carries `ts` — kept so history shows real send times. */
function historyToItems(messages: any[]): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndexByCallId = new Map<string, number>()
  // a "run" = a user message and everything until the next one. Tool deliverables buffer
  // for the whole run and attach to that run's FINAL assistant answer — matching the live
  // path (flush at agent_end), so multi-turn / repeated answers don't strand the media.
  let pending: Artifact[] = []
  let runLastTextIdx = -1

  const flushRun = () => {
    if (pending.length) {
      if (runLastTextIdx >= 0) {
        const have = new Set((items[runLastTextIdx].artifacts || []).map((a) => a.path))
        const add = pending.filter((a) => !have.has(a.path))
        if (add.length) {
          items[runLastTextIdx] = {
            ...items[runLastTextIdx],
            artifacts: [...(items[runLastTextIdx].artifacts || []), ...add]
          }
        }
      } else {
        attachToLastAssistant(items, pending, items[items.length - 1]?.ts ?? 0)
      }
    }
    pending = []
    runLastTextIdx = -1
  }

  for (const message of messages) {
    const ts = toMs(message.ts)
    if (message.role === 'user') {
      flushRun() // the previous run ended
      const atts = (message.attachments || []) as Artifact[]  // files the user attached (by ref)
      items.push({ kind: 'user', text: String(message.content ?? ''), ts, ...(atts.length ? { artifacts: atts } : {}) })
    } else if (message.role === 'assistant') {
      for (const block of message.content || []) {
        if (block.type === 'text' && block.text) {
          runLastTextIdx = items.length // track the run's latest answer bubble
          items.push({ kind: 'assistant', text: block.text, streaming: false, ts })
        } else if (block.type === 'thinking' && block.thinking) {
          items.push({ kind: 'thinking', text: block.thinking, streaming: false, ts })
        } else if (block.type === 'toolCall') {
          toolIndexByCallId.set(String(block.id), items.length)
          items.push({ kind: 'tool', name: block.name || '?', args: block.arguments || {},
                       result: '', isError: false, done: false, ts })
        }
      }
      if (message.errorMessage) {
        items.push({ kind: 'system', tone: 'error', text: String(message.errorMessage), ts })
      }
    } else if (message.role === 'toolResult') {
      const text = (message.content || []).map((block: any) => block?.text || '').join('')
      // tool block is text-only; buffer its DECLARED deliverables for the run's answer
      for (const a of message.artifacts || []) {
        if (!pending.some((p) => p.path === a.path)) pending.push(a)
      }
      const index = toolIndexByCallId.get(String(message.toolCallId))
      if (index !== undefined && items[index]?.kind === 'tool') {
        const tool = items[index] as Extract<ChatItem, { kind: 'tool' }>
        items[index] = { ...tool, result: text, isError: !!message.isError, done: true }
      } else {
        // orphan result (no matching call in this transcript) — show it anyway
        items.push({ kind: 'tool', name: message.toolName || '?', args: {}, result: text, isError: !!message.isError, done: true, ts })
      }
    }
  }
  flushRun() // the final run
  return items
}

/** Incoming artifacts not already waiting in the pending buffer — dedupes WITHIN a run
 *  (so the model declaring the same file twice shows it once) while still allowing a
 *  later turn to re-present a file the user asks to see again. */
function newArtifacts(session: SessionState, incoming?: Artifact[]): Artifact[] | undefined {
  if (!incoming?.length) return undefined
  const seen = new Set((session.pendingArtifacts || []).map((a) => a.path))
  const fresh = incoming.filter((a) => !seen.has(a.path))
  return fresh.length ? fresh : undefined
}

/** Attach deliverables to the last assistant bubble (walking from the end); if there is
 *  no assistant item yet, push a bare one to carry them. Dedupes against what's there. */
function attachToLastAssistant(items: ChatItem[], artifacts: Artifact[], ts: number): void {
  if (!artifacts.length) return
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'assistant') {
      const have = new Set((items[i].artifacts || []).map((a) => a.path))
      const add = artifacts.filter((a) => !have.has(a.path))
      if (add.length) items[i] = { ...items[i], artifacts: [...(items[i].artifacts || []), ...add] }
      return
    }
  }
  items.push({ kind: 'assistant', text: '', streaming: false, ts, artifacts })
}

interface AppState {
  flavor: FlavorInfo | null
  supervisor: SupervisorStatus
  connection: 'idle' | 'connecting' | 'open' | 'closed'
  hello: Hello | null
  view: View
  /** Settings deep-link — a tool's gear icon jumps to Tools & plugins filtered to that tool; null = none */
  settingsTarget: { tab: string; query: string } | null
  theme: Theme

  agents: AgentInfo[]
  currentAgentId: string
  /** the CURRENT agent's chats (per-agent list; used by agent-scoped views) */
  sessionRows: SessionRow[]
  /** ALL chats across every agent, newest first (cross-agent Recents). Each row carries its
   *  agentId so resuming switches currentAgentId to the row's owner. Populated by refreshRecents. */
  recents: SessionRow[]
  currentSessionKey: string
  /** project the CURRENT chat belongs to ('' = standalone) — sent with chat.send */
  currentProjectId: string
  /** which agent the agent-detail page (view:'agent') is showing — set by clicking a sidebar agent */
  viewedAgentId: string
  projects: ProjectRow[]
  sessions: Record<string, SessionState>
  /** Chrome-style tabs: chats opened this app-session, in tab order */
  openTabs: OpenTab[]
  /** session key -> last known title; survives agent switches (sessionRows doesn't) */
  tabTitles: Record<string, string>
  sidebarCollapsed: boolean
  /** the right-side Canvas: a file open for rich view/edit (null = closed) + its width */
  canvas: { artifact: Artifact | null; width: number }

  /** self-declared canvas actions (e.g. Convert to Vector), fetched from the plugin catalog at
   *  handshake — present only for tools that are actually installed. Rendered as artifact buttons. */
  artifactActions: ArtifactAction[]
  /** artifact path -> true while its action is running (spinner + disabled button) */
  artifactActionBusy: Record<string, boolean>

  catalog: CatalogBundle[]
  catalogError: string
  installed: InstalledBundle[]
  installBusy: Record<string, string>

  notifications: NotificationRow[]

  bootstrap(): Promise<void>
  setView(view: View): void
  /** open Settings on a specific tab, filtered to a tool (a tool's gear icon uses this) */
  openToolConfig(toolName: string): void
  clearSettingsTarget(): void
  toggleTheme(): void
  toggleSidebar(): void
  openCanvas(artifact: Artifact): void
  closeCanvas(): void
  setCanvasWidth(width: number): void
  /** run a self-declared artifact action (e.g. Convert to Vector) on an artifact, then open the
   *  primary produced artifact (the SVG) in the Canvas. Never throws — success OR failure is shown
   *  in the Canvas (there is no toast system), so the click is never a silent no-op. */
  runArtifactAction(action: ArtifactAction, a: Artifact): Promise<void>
  activateTab(tab: OpenTab): Promise<void>
  closeTab(sessionId: string): void
  closeOtherTabs(sessionId: string): void
  closeTabsToRight(sessionId: string): void
  closeTabsToLeft(sessionId: string): void
  closeAllTabs(): void
  reorderTabs(from: string, to: string): void
  selectAgent(agentId: string): Promise<void>
  /** open an agent's DETAIL page (view:'agent') — browse its chats/workspace/skills, does NOT
   *  start or switch a chat (that's selectAgent / "New chat with agent" on the page) */
  viewAgent(agentId: string): void
  /** open a project's DETAIL page (view:'project') */
  openProject(projectId: string): void
  /** start a FRESH chat as the given agent (from the agent detail page's "New chat with …") */
  newChatWithAgent(agentId: string): void
  /** the sidebar "New chat" — a general chat with the default generalist ('main'), regardless of
   *  which agent you were last on (the flavor's default_agent can be a specialist like
   *  figure-creator; a plain New chat should still be the generalist) */
  newChat(): void
  createAgent(fields: { name: string; description?: string; identity?: string }): Promise<string>
  newSession(projectId?: string): void
  resumeSession(sessionId: string): Promise<void>
  renameSession(sessionId: string, title: string): Promise<void>
  deleteSession(sessionId: string): Promise<void>
  moveSession(sessionId: string, projectId: string): Promise<void>
  duplicateSession(sessionId: string): Promise<void>
  exportSessionMd(sessionId: string): Promise<void>
  createProject(name: string): Promise<void>
  renameProject(projectId: string, name: string): Promise<void>
  deleteProject(projectId: string): Promise<void>
  /** Layer B: set the project's lead agent ('' clears — falls back to main) */
  setProjectLead(projectId: string, agentId: string): Promise<void>
  addProjectMember(projectId: string, agentId: string): Promise<void>
  removeProjectMember(projectId: string, agentId: string): Promise<void>
  sendMessage(text: string, attachments?: OutgoingAttachment[]): Promise<void>
  abortRun(): Promise<void>
  /** load text into the chat composer (a user message's Edit action); `n` forces a fresh apply
   *  even when the same text is edited twice. */
  composerSeed: { text: string; n: number } | null
  seedComposer(text: string): void
  refreshCatalog(): Promise<void>
  installBundle(id: string): Promise<void>
  uninstallBundle(id: string): Promise<void>
}

export const useApp = create<AppState>((set, get) => {
  // ---- event plumbing (registered once at bootstrap) --------------------------

  /** Close a set of tabs; if the ACTIVE chat was among them, re-activate the nearest
   *  surviving tab (prefer to the right of `anchorIdx`, then left), or open a fresh chat
   *  when none remain. Shared by every close variant (single / others / left / right / all). */
  function closeTabs(closeIds: Set<string>, anchorIdx: number): void {
    const { openTabs, currentSessionKey } = get()
    const survivors = openTabs.filter((t) => !closeIds.has(t.id))
    set({ openTabs: survivors })
    if (!closeIds.has(currentSessionKey)) return // the active chat stayed open — nothing to do
    if (survivors.length === 0) {
      get().newSession()
      return
    }
    const right = openTabs.slice(anchorIdx).find((t) => !closeIds.has(t.id))
    const left = [...openTabs.slice(0, anchorIdx)].reverse().find((t) => !closeIds.has(t.id))
    void get().activateTab(right || left || survivors[survivors.length - 1])
  }

  function patchSession(sessionKey: string, patch: (session: SessionState) => SessionState): void {
    set((state) => ({
      sessions: {
        ...state.sessions,
        [sessionKey]: patch(state.sessions[sessionKey] || { items: [], running: false })
      }
    }))
  }

  function appendStreaming(sessionKey: string, kind: 'assistant' | 'thinking', delta: string, ts: number): void {
    patchSession(sessionKey, (session) => {
      const items = [...session.items]
      const last = items[items.length - 1]
      if (last && last.kind === kind && last.streaming) {
        items[items.length - 1] = { ...last, text: last.text + delta }
      } else {
        items.push({ kind, text: delta, streaming: true, ts } as ChatItem)
      }
      return { ...session, items }
    })
  }

  function handleAgentEvent(sessionKey: string, event: AgentEvent, ts: number): void {
    switch (event.type) {
      case 'message_update':
        if (event.kind === 'text_delta') appendStreaming(sessionKey, 'assistant', event.delta || '', ts)
        else if (event.kind === 'thinking_delta') appendStreaming(sessionKey, 'thinking', event.delta || '', ts)
        break
      case 'message_end':
        // just end streaming here — declared deliverables stay buffered and attach to the
        // FINAL answer at agent_end (flushing per-turn latched them onto intermediate turns
        // when the model took several turns / repeated itself)
        patchSession(sessionKey, (session) => ({
          ...session,
          items: session.items.map((item) =>
            'streaming' in item && item.streaming ? { ...item, streaming: false } : item
          )
        }))
        break
      case 'model_trace':
        // update the persistent status strip (model + token usage), NOT the scrollback — Claude
        // shows this live in a status bar and hides it once the step is over, never archiving a
        // per-step line in the transcript. Each trace REPLACES the last (in-place, one indicator).
        patchSession(sessionKey, (session) => ({
          ...session,
          usage: {
            model: String(event.model || session.usage?.model || ''),
            tokensIn: Number(event.tokensIn || 0),
            tokensOut: Number(event.tokensOut || 0),
            step: Number(event.step || 0)
          }
        }))
        break
      case 'tool_execution_start':
        patchSession(sessionKey, (session) => ({
          ...session,
          items: [
            ...session.items,
            { kind: 'tool', name: event.toolName || '?', args: event.args || {}, result: '', isError: false, done: false, ts }
          ]
        }))
        break
      case 'tool_progress':
        // a running tool's incremental steps (the computer tool's per-step updates, GuardedTool
        // retries, …) — accumulate onto the matching not-yet-done tool block so they render live
        patchSession(sessionKey, (session) => {
          const items = [...session.items]
          const text = String(event.text || '').trim()
          if (text) {
            for (let i = items.length - 1; i >= 0; i--) {
              const item = items[i]
              if (item.kind === 'tool' && !item.done && item.name === (event.toolName || '?')) {
                items[i] = { ...item, progress: item.progress ? `${item.progress}\n${text}` : text }
                break
              }
            }
          }
          return { ...session, items }
        })
        break
      case 'tool_execution_end':
        patchSession(sessionKey, (session) => {
          const items = [...session.items]
          for (let i = items.length - 1; i >= 0; i--) {
            const item = items[i]
            if (item.kind === 'tool' && !item.done && item.name === (event.toolName || '?')) {
              // tool block is text-only; its deliverables buffer for the coming answer
              items[i] = { ...item, result: resultText(event.result), isError: !!event.isError, done: true }
              break
            }
          }
          const fresh = newArtifacts(session, event.artifacts)
          const pendingArtifacts = fresh ? [...(session.pendingArtifacts || []), ...fresh] : session.pendingArtifacts
          return { ...session, items, pendingArtifacts }
        })
        break
      case 'subagent_event':
        patchSession(sessionKey, (session) => {
          const agent = event.childAgent
          const items = [...session.items]
          // attach to the most recent STILL-RUNNING block for this child; otherwise open a new one
          let idx = -1
          for (let i = items.length - 1; i >= 0; i--) {
            const it = items[i]
            if (it.kind === 'subagent' && it.agent === agent && it.status === 'running') { idx = i; break }
          }
          if (event.kind === 'start' || idx < 0) {
            // a fresh delegation (or a stray beat before its start) → new grouped block
            items.push({ kind: 'subagent', agent, steps: event.kind === 'tool' && event.tool ? [event.tool] : [], status: event.kind === 'error' ? 'error' : event.kind === 'done' ? 'done' : 'running', detail: event.detail, ts })
          } else {
            const prev = items[idx] as Extract<ChatItem, { kind: 'subagent' }>
            if (event.kind === 'tool' && event.tool) {
              items[idx] = { ...prev, steps: [...prev.steps, event.tool] }
            } else if (event.kind === 'done' || event.kind === 'error') {
              items[idx] = { ...prev, status: event.kind, detail: event.detail }
            }
          }
          return { ...session, items }
        })
        break
      case 'agent_end': {
        const error = event.stopReason === 'error' ? String(event.error || 'run failed') : ''
        patchSession(sessionKey, (session) => {
          const items = error
            ? [...session.items, { kind: 'system' as const, tone: 'error' as const, text: error, ts }]
            : session.items.map((item) =>
                'streaming' in item && item.streaming ? { ...item, streaming: false } : item
              )
          // flush the run's DECLARED deliverables onto its final answer (once, at run end)
          if (session.pendingArtifacts?.length) attachToLastAssistant(items, session.pendingArtifacts, ts)
          return { items, running: false, pendingArtifacts: [] }
        })
        // desktop notification for a watched chat (an open tab / the current chat) finishing —
        // NOT for background sub-agent / cron / heartbeat runs, and only when tabbed away
        if (!error) {
          const st = get()
          if (st.currentSessionKey === sessionKey || st.openTabs.some((t) => t.id === sessionKey)) {
            desktopNotify(st.flavor?.productName || 'agentd', 'Your agent finished responding.')
          }
        }
        break
      }
      default:
        break
    }
  }

  async function handshake(): Promise<void> {
    const hello = (await gateway.request<Hello>('hello')) as Hello
    const flavor = get().flavor
    const preferred = get().currentAgentId || flavor?.defaultAgent || hello.agentId
    const agentIds = new Set(hello.agents.map((agent) => agent.id))
    set({
      hello,
      agents: hello.agents,
      currentAgentId: agentIds.has(preferred) ? preferred : hello.agentId
    })
    await Promise.all([refreshSessions(), refreshRecents(), refreshProjects()])
    await preinstallBundles()
    await refreshArtifactActions()
  }

  // Learn which canvas ACTIONS exist from the live plugin catalog: any enabled tool that
  // self-declares an `artifactAction`. Present only for installed tools (e.g. figure_to_svg ships
  // with figure-creator), so the button appears exactly when the capability is installed.
  async function refreshArtifactActions(): Promise<void> {
    try {
      const { plugins } = await gateway.request<{
        plugins: {
          enabled: boolean
          tools: { name: string; enabled: boolean; artifactAction?: ArtifactAction | null }[]
        }[]
      }>('plugins.catalog')
      const actions: ArtifactAction[] = []
      for (const p of plugins || []) {
        if (p.enabled === false) continue
        for (const t of p.tools || []) {
          const a = t.artifactAction
          if (t.enabled !== false && a && Array.isArray(a.mime) && a.mime.length && a.label) {
            actions.push({ tool: t.name, mime: a.mime, label: a.label, param: a.param || 'image' })
          }
        }
      }
      set({ artifactActions: actions })
    } catch {
      /* older daemon / no catalog — just no action buttons */
    }
  }

  async function refreshSessions(): Promise<void> {
    const { currentAgentId } = get()
    try {
      const payload = await gateway.request<{ sessions: SessionRow[] }>('sessions.list', {
        agentId: currentAgentId
      })
      // normalize agentId (an older daemon may omit it) so downstream never sees undefined
      const rows = (payload.sessions || []).map((r) => ({ ...r, agentId: r.agentId || currentAgentId }))
      // fold titles into the cross-agent cache so tabs keep their names after
      // switching agents (rows are per-agent; the cache is not)
      set((state) => ({
        sessionRows: rows,
        tabTitles: {
          ...state.tabTitles,
          ...Object.fromEntries(rows.filter((r) => r.title).map((r) => [r.sessionId, r.title]))
        }
      }))
    } catch {
      set({ sessionRows: [] })
    }
  }

  /** Cross-agent Recents: EVERY agent's chats, newest first (internal agent-to-agent / cron
   *  sessions are excluded server-side). Rows carry agentId so resuming opens the right agent. */
  async function refreshRecents(): Promise<void> {
    try {
      const payload = await gateway.request<{ sessions: SessionRow[] }>('sessions.list', {
        all: true
      })
      // normalize agentId (older daemon may omit it) so the agent dot / resume never break
      const rows = (payload.sessions || []).map((r) => ({ ...r, agentId: r.agentId || '' }))
      set((state) => ({
        recents: rows,
        // keep tab titles fresh across the whole set (tabs can span agents)
        tabTitles: {
          ...state.tabTitles,
          ...Object.fromEntries(rows.filter((r) => r.title).map((r) => [r.sessionId, r.title]))
        }
      }))
    } catch {
      set({ recents: [] })
    }
  }

  async function refreshProjects(): Promise<void> {
    try {
      const payload = await gateway.request<{ projects: ProjectRow[] }>('projects.list')
      set({ projects: payload.projects || [] })
    } catch {
      set({ projects: [] })
    }
  }

  /** The agent that OWNS a chat (its transcript partition). Recents are cross-agent, so a ⋯-menu
   *  action on a Recents row can target a DIFFERENT agent than the one we're on — every session
   *  RPC must send the row's own agentId, not currentAgentId. Falls back to the current agent. */
  function agentOf(sessionId: string): string {
    const st = get()
    const row =
      st.recents.find((r) => r.sessionId === sessionId) ||
      st.sessionRows.find((r) => r.sessionId === sessionId)
    return row?.agentId || st.currentAgentId
  }

  /** Studio flavors ship .agentpkg files in resources/bundles — install any that are
   *  missing on first run (idempotent: the ledger says what's already there). */
  async function preinstallBundles(): Promise<void> {
    const flavor = get().flavor
    if (!flavor || flavor.bundledPackages.length === 0) return
    try {
      const { bundles } = await gateway.request<{ bundles: InstalledBundle[] }>('marketplace.installed')
      const have = new Set((bundles || []).map((bundle) => bundle.id))
      for (const packagePath of flavor.bundledPackages) {
        const fileName = packagePath.replace(/\\/g, '/').split('/').pop() || ''
        const bundleId = fileName.replace(/-[0-9][^-]*\.agentpkg$/, '')
        if (!bundleId || have.has(bundleId)) continue
        set((state) => ({ installBusy: { ...state.installBusy, [bundleId]: 'installing…' } }))
        try {
          await gateway.request('marketplace.install', { file: packagePath })
        } finally {
          set((state) => {
            const busy = { ...state.installBusy }
            delete busy[bundleId]
            return { installBusy: busy }
          })
        }
      }
    } catch {
      /* store disabled / no marketplace — fine */
    }
  }

  let wired = false
  function wireEvents(): void {
    if (wired) return
    wired = true
    gateway.on('chat.event', (payload) => {
      // the server stamps every live event (epoch seconds) so all clients agree on time
      const ts = typeof payload.ts === 'number' ? payload.ts * 1000 : Date.now()
      handleAgentEvent(String(payload.sessionKey || ''), (payload.event || {}) as AgentEvent, ts)
    })
    gateway.on('agents.changed', (payload) => {
      const agents = (payload.agents as AgentInfo[]) || []
      set({ agents })
      // if the agent we're on vanished (deleted here or elsewhere), fall back to
      // main so we never keep requesting a now-unknown agent's sessions
      const cur = get().currentAgentId
      if (cur && agents.length && !agents.some((a) => a.id === cur)) {
        void get().selectAgent('main')
      }
    })
    gateway.on('sessions.changed', () => {
      // renamed / auto-titled / deleted (possibly by another client) — refresh both the
      // per-agent list and the cross-agent Recents
      void refreshSessions()
      void refreshRecents()
    })
    gateway.on('projects.changed', () => {
      void refreshProjects()
    })
    gateway.on('marketplace.progress', (payload) => {
      const id = String(payload.id || '')
      if (!id) return
      set((state) => ({
        installBusy: { ...state.installBusy, [id]: String(payload.message || payload.step || '') }
      }))
    })
    gateway.on('notification', (payload) => {
      set((state) => ({
        notifications: [payload as NotificationRow, ...state.notifications].slice(0, 50)
      }))
    })
    gateway.onStatus((status) => {
      set({ connection: status })
      if (status === 'open') void handshake()
    })
  }

  // ---- the store ---------------------------------------------------------------

  return {
    flavor: null,
    supervisor: { phase: 'looking', message: 'starting…' },
    connection: 'idle',
    hello: null,
    view: 'chat',
    settingsTarget: null,
    theme: initialTheme(),

    agents: [],
    currentAgentId: '',
    sessionRows: [],
    recents: [],
    currentSessionKey: newSessionKey(),
    currentProjectId: '',
    viewedAgentId: '',
    projects: [],
    sessions: {},
    composerSeed: null,
    openTabs: [],
    tabTitles: {},
    sidebarCollapsed: false,
    canvas: { artifact: null, width: 560 },

    artifactActions: [],
    artifactActionBusy: {},

    catalog: [],
    catalogError: '',
    installed: [],
    installBusy: {},

    notifications: [],

    async bootstrap() {
      applyTheme(get().theme)   // a persisted 'dark' shows from the first paint
      const flavor = (await window.agentd.flavor()) as FlavorInfo
      set({ flavor })
      window.agentd.onSupervisorStatus((status) => set({ supervisor: status as SupervisorStatus }))
      set({ supervisor: (await window.agentd.supervisorStatus()) as SupervisorStatus })
      wireEvents()
      set({ connection: 'connecting' })
      // Re-resolve host/port/token on every (re)connect: ensureDaemon finds the live
      // daemon (or starts one) and returns its CURRENT url+token — so a daemon restart
      // (which rotates the token) reconnects cleanly instead of looping on a stale one.
      gateway.connect(async () => {
        const { url } = await window.agentd.ensureDaemon()
        setGatewayUrl(url) // keep artifact/file URLs pointed at the live daemon (port+token)
        return url
      })
    },

    setView(view) {
      set({ view })
      if (view === 'store') void get().refreshCatalog()
    },

    openToolConfig(toolName) {
      // a tool's gear icon → Settings ▸ Tools & plugins, filtered to that tool
      set({ view: 'settings', settingsTarget: { tab: 'tools', query: toolName } })
    },
    clearSettingsTarget() {
      set({ settingsTarget: null })
    },

    toggleTheme() {
      const next: Theme = get().theme === 'light' ? 'dark' : 'light'
      applyTheme(next)
      set({ theme: next })
    },

    toggleSidebar() {
      set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed }))
    },

    openCanvas(artifact) {
      set((s) => ({ canvas: { ...s.canvas, artifact } }))
    },
    async runArtifactAction(action, a) {
      if (get().artifactActionBusy[a.path]) return
      set((s) => ({ artifactActionBusy: { ...s.artifactActionBusy, [a.path]: true } }))
      try {
        const res = await gateway.request<{ text: string; artifacts: Artifact[] }>('tools.invoke', {
          name: action.tool,
          params: { [action.param]: a.path }
        })
        const arts = res.artifacts || []
        // open the primary editable deliverable — prefer the SVG, else the first artifact. If the
        // tool produced no file, show its text so the result is never silent.
        const primary = arts.find((x) => x.name.toLowerCase().endsWith('.svg')) || arts[0]
        if (primary) get().openCanvas(primary)
        else
          get().openCanvas({
            // `.md` so the Canvas routes it to the text viewer and actually shows `text`
            path: `result:${a.path}:${Date.now()}`,
            name: `${action.label} — result.md`,
            mime: 'text/markdown',
            kind: 'file',
            text: res.text || 'Done (no file produced).'
          })
      } catch (err) {
        // surface the failure IN the canvas (there is no toast system) so it's never a silent no-op.
        // NOTE: name it `.md` — the canvas picks the viewer by extension, so a bare name renders the
        // "no preview" fallback and hides the message (that was the earlier bug).
        get().openCanvas({
          path: `error:${a.path}:${Date.now()}`,
          name: `${action.label} failed.md`,
          mime: 'text/markdown',
          kind: 'file',
          text:
            `## ${action.label} failed\n\n` +
            '```\n' +
            String((err as Error)?.message || err) +
            '\n```\n\n' +
            `It ran \`${action.tool}\` directly on:\n\n\`${a.path}\`\n\n` +
            `**Common causes**\n` +
            `- the source file no longer exists at that path (an old/moved artifact) → "no such file"\n` +
            `- the image model / OCR is unavailable (e.g. a Gemini spend cap) → "RESOURCE_EXHAUSTED"\n` +
            `- the tool isn't installed / registered → "tool not available"`
        })
      } finally {
        set((s) => {
          const busy = { ...s.artifactActionBusy }
          delete busy[a.path]
          return { artifactActionBusy: busy }
        })
      }
    },
    closeCanvas() {
      set((s) => ({ canvas: { ...s.canvas, artifact: null } }))
    },
    setCanvasWidth(width) {
      set((s) => ({ canvas: { ...s.canvas, width: Math.max(360, Math.min(1100, Math.round(width))) } }))
    },

    async activateTab(tab) {
      // a tab may belong to ANOTHER agent — switch context first so history
      // loads from (and messages route to) the right one
      if (get().currentAgentId !== tab.agentId) {
        set({ currentAgentId: tab.agentId })
        await refreshSessions()
      }
      await get().resumeSession(tab.id)
    },

    closeTab(sessionId) {
      const { openTabs } = get()
      const idx = openTabs.findIndex((t) => t.id === sessionId)
      closeTabs(new Set([sessionId]), idx)
    },

    // Chrome-style bulk closes — all funnel through closeTabs(), which keeps the survivors,
    // and (only if the active chat was among those closed) re-activates the nearest survivor
    // or opens a fresh chat when none remain.
    closeOtherTabs(sessionId) {
      const { openTabs } = get()
      const ids = new Set(openTabs.filter((t) => t.id !== sessionId).map((t) => t.id))
      closeTabs(ids, openTabs.findIndex((t) => t.id === sessionId))
    },
    closeTabsToRight(sessionId) {
      const { openTabs } = get()
      const idx = openTabs.findIndex((t) => t.id === sessionId)
      if (idx < 0) return
      closeTabs(new Set(openTabs.slice(idx + 1).map((t) => t.id)), idx)
    },
    closeTabsToLeft(sessionId) {
      const { openTabs } = get()
      const idx = openTabs.findIndex((t) => t.id === sessionId)
      if (idx < 0) return
      closeTabs(new Set(openTabs.slice(0, idx).map((t) => t.id)), idx)
    },
    closeAllTabs() {
      closeTabs(new Set(get().openTabs.map((t) => t.id)), 0)
    },

    reorderTabs(from, to) {
      set((s) => {
        const a = [...s.openTabs]
        const fi = a.findIndex((t) => t.id === from)
        const ti = a.findIndex((t) => t.id === to)
        if (fi < 0 || ti < 0) return {}
        const [moved] = a.splice(fi, 1)
        a.splice(ti, 0, moved)
        return { openTabs: a }
      })
    },

    async selectAgent(agentId) {
      // LM-Studio behaviour: clicking an agent OPENS where you left off — its most
      // recent conversation — not an empty screen. No history -> a fresh chat.
      set({ currentAgentId: agentId, view: 'chat' })
      await refreshSessions()
      const latest = get().sessionRows[0]
      if (latest) {
        await get().resumeSession(latest.sessionId)
      } else {
        get().newSession()   // registers the tab too — the active chat always has one
      }
    },

    viewAgent(agentId) {
      // open the agent's detail PAGE — browse only; does NOT switch the chat agent
      set({ view: 'agent', viewedAgentId: agentId })
    },

    openProject(projectId) {
      set({ view: 'project', currentProjectId: projectId })
    },

    newChatWithAgent(agentId) {
      const changed = get().currentAgentId !== agentId
      set({ currentAgentId: agentId })
      if (changed) void refreshSessions()
      get().newSession() // fresh chat as this agent (sets view:'chat', registers the tab)
    },

    newChat() {
      // 'main' is the canonical generalist (config.agent_id default + always-synthesized agent).
      // A plain "New chat" goes here even when the install's default_agent is a specialist.
      get().newChatWithAgent('main')
    },

    async createAgent(fields) {
      // server scaffolds the definition + assigns colour/tagline; agents.changed
      // refreshes the list. Throws on a server error so the modal can show it.
      const res = await gateway.request<{ created: boolean; agentId?: string; error?: string }>(
        'agents.create',
        { name: fields.name, description: fields.description || '', identity: fields.identity || '' }
      )
      if (!res.created) throw new Error(res.error || 'could not create the agent')
      if (res.agentId) await get().selectAgent(res.agentId)
      return res.agentId || ''
    },

    newSession(projectId?: string) {
      // fresh chat — inside a project when one is given, standalone otherwise.
      // Layer B: messaging a PROJECT talks to its LEAD agent (defaultAgentId, fallback main);
      // a standalone new chat keeps the current agent.
      const key = newSessionKey()
      let agentId = get().currentAgentId
      if (projectId) {
        const project = get().projects.find((p) => p.id === projectId)
        const lead = project?.defaultAgentId || 'main'
        if (get().agents.some((a) => a.id === lead)) agentId = lead
      }
      const switched = agentId !== get().currentAgentId
      set((s) => ({
        currentAgentId: agentId,
        currentSessionKey: key,
        currentProjectId: projectId || '',
        view: 'chat',
        openTabs: s.openTabs.some((t) => t.id === key)
          ? s.openTabs
          : [...s.openTabs, { id: key, agentId }]
      }))
      if (switched) void refreshSessions()
    },

    async renameSession(sessionId, title) {
      // optimistic: update the row (in BOTH lists) + the tab-title cache now; the server
      // confirms via sessions.changed
      const agentId = agentOf(sessionId)
      const patch = (row: SessionRow): SessionRow =>
        row.sessionId === sessionId ? { ...row, title, titleManual: !!title.trim() } : row
      set((state) => ({
        sessionRows: state.sessionRows.map(patch),
        recents: state.recents.map(patch),
        tabTitles: title.trim()
          ? { ...state.tabTitles, [sessionId]: title }
          : state.tabTitles
      }))
      try {
        await gateway.request('sessions.rename', {
          sessionKey: sessionId,
          agentId: agentId || undefined,
          title
        })
      } catch {
        void refreshSessions() // failed — reload the truth
      }
    },

    async deleteSession(sessionId) {
      // capture the owning agent BEFORE we drop the row from the lists
      const agentId = agentOf(sessionId)
      // optimistic removal (both lists); the server broadcasts sessions.changed as confirmation
      set((state) => {
        const sessions = { ...state.sessions }
        delete sessions[sessionId]
        return {
          sessionRows: state.sessionRows.filter((row) => row.sessionId !== sessionId),
          recents: state.recents.filter((row) => row.sessionId !== sessionId),
          sessions,
          openTabs: state.openTabs.filter((t) => t.id !== sessionId)
        }
      })
      if (get().currentSessionKey === sessionId) {
        set({ currentSessionKey: newSessionKey(), currentProjectId: '' })
      }
      try {
        await gateway.request('sessions.delete', {
          sessionKey: sessionId,
          agentId: agentId || undefined
        })
      } catch {
        void refreshSessions() // failed (e.g. active run) — reload the truth
      }
    },

    async moveSession(sessionId, projectId) {
      const agentId = agentOf(sessionId)
      // optimistic re-group (both lists); server confirms + rebroadcasts via sessions.changed
      const patch = (row: SessionRow): SessionRow =>
        row.sessionId === sessionId ? { ...row, projectId } : row
      set((state) => ({
        sessionRows: state.sessionRows.map(patch),
        recents: state.recents.map(patch)
      }))
      try {
        await gateway.request('sessions.move', {
          sessionKey: sessionId,
          agentId: agentId || undefined,
          projectId
        })
      } catch {
        void refreshSessions()
      }
    },

    async duplicateSession(sessionId) {
      // the copy lands via the server's sessions.changed broadcast (refreshRecents/Sessions)
      try {
        await gateway.request('sessions.duplicate', {
          sessionKey: sessionId,
          agentId: agentOf(sessionId) || undefined
        })
      } catch {
        void refreshSessions()
      }
    },

    async exportSessionMd(sessionId) {
      const st = get()
      const row =
        st.recents.find((r) => r.sessionId === sessionId) ||
        st.sessionRows.find((r) => r.sessionId === sessionId)
      const title = row?.title || sessionId
      try {
        const payload = await gateway.request<{ messages: any[] }>('sessions.history', {
          sessionKey: sessionId,
          agentId: (row?.agentId || st.currentAgentId) || undefined
        })
        downloadTextFile(`${safeFileName(title)}.md`, sessionToMarkdown(title, payload.messages || []))
      } catch {
        // history unavailable (daemon busy / gone) — nothing to export
      }
    },

    async createProject(name) {
      const trimmed = name.trim()
      if (!trimmed) return
      try {
        const { project } = await gateway.request<{ project: ProjectRow }>('projects.create', {
          name: trimmed
        })
        await refreshProjects()
        // start working in it right away — a fresh chat inside the new project
        if (project?.id) get().newSession(project.id)
      } catch {
        await refreshProjects()
      }
    },

    async renameProject(projectId, name) {
      set((state) => ({
        projects: state.projects.map((p) => (p.id === projectId ? { ...p, name } : p))
      }))
      try {
        await gateway.request('projects.rename', { id: projectId, name })
      } catch {
        void refreshProjects()
      }
    },

    async deleteProject(projectId) {
      // deletes the FOLDER; its chats become standalone (server behaviour)
      set((state) => ({
        projects: state.projects.filter((p) => p.id !== projectId),
        currentProjectId: state.currentProjectId === projectId ? '' : state.currentProjectId
      }))
      try {
        await gateway.request('projects.delete', { id: projectId })
      } catch {
        void refreshProjects()
      }
    },

    async setProjectLead(projectId, agentId) {
      // optimistic; the server confirms via projects.changed → refreshProjects
      set((state) => ({
        projects: state.projects.map((p) => (p.id === projectId ? { ...p, defaultAgentId: agentId } : p))
      }))
      try {
        await gateway.request('projects.setLead', { id: projectId, agentId })
      } catch {
        void refreshProjects()
      }
    },

    async addProjectMember(projectId, agentId) {
      set((state) => ({
        projects: state.projects.map((p) =>
          p.id === projectId ? { ...p, members: [...new Set([...(p.members || []), agentId])] } : p
        )
      }))
      try {
        await gateway.request('projects.addMember', { id: projectId, agentId })
      } catch {
        void refreshProjects()
      }
    },

    async removeProjectMember(projectId, agentId) {
      set((state) => ({
        projects: state.projects.map((p) =>
          p.id === projectId ? { ...p, members: (p.members || []).filter((m) => m !== agentId) } : p
        )
      }))
      try {
        await gateway.request('projects.removeMember', { id: projectId, agentId })
      } catch {
        void refreshProjects()
      }
    },

    async resumeSession(sessionId) {
      // Recents are cross-agent, so this row may belong to a DIFFERENT agent than the one
      // we're on. Resolve the row from either list and switch currentAgentId to its owner
      // BEFORE loading history (the history RPC is agent-scoped) — else the chat loads empty.
      const prevAgent = get().currentAgentId
      const row =
        get().recents.find((r) => r.sessionId === sessionId) ||
        get().sessionRows.find((r) => r.sessionId === sessionId)
      const agentId = row?.agentId || prevAgent
      set((state) => ({
        currentAgentId: agentId,
        currentSessionKey: sessionId,
        currentProjectId: row?.projectId || '',
        view: 'chat',
        openTabs: state.openTabs.some((t) => t.id === sessionId)
          ? state.openTabs
          : [...state.openTabs, { id: sessionId, agentId }]
      }))
      // switched agents → refresh the per-agent list so agent-scoped views stay coherent
      if (agentId !== prevAgent) void refreshSessions()
      // already have this session in memory with content (it's live, or we loaded it
      // before) — don't clobber it by reloading.
      const existing = get().sessions[sessionId]
      if (existing && existing.items.length > 0) return
      try {
        const payload = await gateway.request<{ messages: any[] }>('sessions.history', {
          sessionKey: sessionId,
          agentId: agentId || undefined
        })
        set((state) => ({
          sessions: {
            ...state.sessions,
            [sessionId]: { items: historyToItems(payload.messages || []), running: false }
          }
        }))
      } catch {
        // couldn't load history (daemon busy / gone) — leave the session empty
        set((state) => ({
          sessions: {
            ...state.sessions,
            [sessionId]: state.sessions[sessionId] || { items: [], running: false }
          }
        }))
      }
    },

    async sendMessage(text, attachments) {
      const { currentSessionKey, currentAgentId, currentProjectId } = get()
      patchSession(currentSessionKey, (session) => ({
        items: [...session.items, { kind: 'user', text, ts: Date.now() }],
        running: true
      }))
      try {
        const res = await gateway.request<{ runId: string; attachments?: Artifact[] }>('chat.send', {
          sessionKey: currentSessionKey,
          message: text,
          agentId: currentAgentId || undefined,
          projectId: currentProjectId || undefined,
          attachments: attachments?.length ? attachments : undefined,
          idempotencyKey: `${currentSessionKey}-${Date.now()}`
        })
        // the daemon saved the uploads and returned their real workspace paths — attach
        // them to the user bubble we just pushed so they render via /file (like history)
        if (res?.attachments?.length) {
          patchSession(currentSessionKey, (session) => {
            const items = [...session.items]
            for (let i = items.length - 1; i >= 0; i--) {
              if (items[i].kind === 'user' && !items[i].artifacts) {
                items[i] = { ...items[i], artifacts: res.attachments }
                break
              }
            }
            return { ...session, items }
          })
        }
      } catch (error) {
        patchSession(currentSessionKey, (session) => ({
          items: [
            ...session.items,
            { kind: 'system', tone: 'error', text: error instanceof Error ? error.message : String(error), ts: Date.now() }
          ],
          running: false
        }))
      }
    },

    async abortRun() {
      const { currentSessionKey } = get()
      try {
        await gateway.request('chat.abort', { sessionKey: currentSessionKey })
      } catch {
        /* no active run — fine */
      }
    },

    seedComposer(text) {
      set({ composerSeed: { text, n: Date.now() } })
    },

    async refreshCatalog() {
      try {
        const [catalog, installed] = await Promise.all([
          gateway.request<{ bundles: CatalogBundle[]; error?: string }>('marketplace.catalog'),
          gateway.request<{ bundles: InstalledBundle[] }>('marketplace.installed')
        ])
        set({
          catalog: catalog.bundles || [],
          catalogError: String(catalog.error || ''),
          installed: installed.bundles || []
        })
      } catch (error) {
        set({ catalogError: error instanceof Error ? error.message : String(error) })
      }
    },

    async installBundle(id) {
      set((state) => ({ installBusy: { ...state.installBusy, [id]: 'starting…' } }))
      try {
        await gateway.request('marketplace.install', { id })
      } catch (error) {
        set({ catalogError: error instanceof Error ? error.message : String(error) })
      } finally {
        set((state) => {
          const busy = { ...state.installBusy }
          delete busy[id]
          return { installBusy: busy }
        })
        await get().refreshCatalog()
      }
    },

    async uninstallBundle(id) {
      set((state) => ({ installBusy: { ...state.installBusy, [id]: 'removing…' } }))
      try {
        await gateway.request('marketplace.uninstall', { id })
      } catch (error) {
        set({ catalogError: error instanceof Error ? error.message : String(error) })
      } finally {
        set((state) => {
          const busy = { ...state.installBusy }
          delete busy[id]
          return { installBusy: busy }
        })
        await get().refreshCatalog()
      }
    }
  }
})
