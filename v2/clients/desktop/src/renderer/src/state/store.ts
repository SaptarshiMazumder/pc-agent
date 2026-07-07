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
import type { Artifact } from '../lib/artifacts'
import { setGatewayUrl } from '../lib/artifacts'
import { downloadTextFile, safeFileName, sessionToMarkdown } from '../lib/exportChat'

export type ChatItem = (
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming: boolean }
  | { kind: 'thinking'; text: string; streaming: boolean }
  | { kind: 'tool'; name: string; args: Record<string, unknown>; result: string; isError: boolean; done: boolean }
  | { kind: 'system'; text: string; tone: 'info' | 'error' }
) & { ts?: number; artifacts?: Artifact[] } // ts: epoch ms (server-side); artifacts: media the item produced

export interface SessionState {
  items: ChatItem[]
  running: boolean
  // deliverables a tool produced, held until the next assistant answer renders them
  // (so the tool log stays pure text and media collects under the answer)
  pendingArtifacts?: Artifact[]
}

/** A file the user is sending TO the agent (e.g. an edited image from the canvas). Bytes
 *  ride the WS send as base64; the daemon saves them to the workspace and hands back real
 *  paths, which then render like any artifact. */
export interface OutgoingAttachment {
  name: string
  mimeType: string
  dataBase64: string
}

export type View = 'chat' | 'store' | 'settings' | 'account' | 'subscription' | 'datasources'

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
  theme: Theme

  agents: AgentInfo[]
  currentAgentId: string
  sessionRows: SessionRow[]
  currentSessionKey: string
  /** project the CURRENT chat belongs to ('' = standalone) — sent with chat.send */
  currentProjectId: string
  projects: ProjectRow[]
  sessions: Record<string, SessionState>
  /** Chrome-style tabs: chats opened this app-session, in tab order */
  openTabs: OpenTab[]
  /** session key -> last known title; survives agent switches (sessionRows doesn't) */
  tabTitles: Record<string, string>
  sidebarCollapsed: boolean
  /** the right-side Canvas: a file open for rich view/edit (null = closed) + its width */
  canvas: { artifact: Artifact | null; width: number }

  catalog: CatalogBundle[]
  catalogError: string
  installed: InstalledBundle[]
  installBusy: Record<string, string>

  notifications: NotificationRow[]

  bootstrap(): Promise<void>
  setView(view: View): void
  toggleTheme(): void
  toggleSidebar(): void
  openCanvas(artifact: Artifact): void
  closeCanvas(): void
  setCanvasWidth(width: number): void
  activateTab(tab: OpenTab): Promise<void>
  closeTab(sessionId: string): void
  closeOtherTabs(sessionId: string): void
  closeTabsToRight(sessionId: string): void
  closeTabsToLeft(sessionId: string): void
  closeAllTabs(): void
  reorderTabs(from: string, to: string): void
  selectAgent(agentId: string): Promise<void>
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
  sendMessage(text: string, attachments?: OutgoingAttachment[]): Promise<void>
  abortRun(): Promise<void>
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
      case 'tool_execution_start':
        patchSession(sessionKey, (session) => ({
          ...session,
          items: [
            ...session.items,
            { kind: 'tool', name: event.toolName || '?', args: event.args || {}, result: '', isError: false, done: false, ts }
          ]
        }))
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
        patchSession(sessionKey, (session) => ({
          ...session,
          items: [
            ...session.items,
            {
              kind: 'system',
              tone: event.kind === 'error' ? 'error' : 'info',
              text:
                event.kind === 'start' ? `subagent ${event.childAgent} started`
                : event.kind === 'tool' ? `subagent ${event.childAgent} · ${event.tool}`
                : event.kind === 'error' ? `subagent ${event.childAgent}: ${event.detail || 'error'}`
                : `subagent ${event.childAgent} done`,
              ts
            }
          ]
        }))
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
    await Promise.all([refreshSessions(), refreshProjects()])
    await preinstallBundles()
  }

  async function refreshSessions(): Promise<void> {
    const { currentAgentId } = get()
    try {
      const payload = await gateway.request<{ sessions: SessionRow[] }>('sessions.list', {
        agentId: currentAgentId
      })
      const rows = payload.sessions || []
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

  async function refreshProjects(): Promise<void> {
    try {
      const payload = await gateway.request<{ projects: ProjectRow[] }>('projects.list')
      set({ projects: payload.projects || [] })
    } catch {
      set({ projects: [] })
    }
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
      // renamed / auto-titled / deleted (possibly by another client) — refresh the list
      void refreshSessions()
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
    theme: initialTheme(),

    agents: [],
    currentAgentId: '',
    sessionRows: [],
    currentSessionKey: newSessionKey(),
    currentProjectId: '',
    projects: [],
    sessions: {},
    openTabs: [],
    tabTitles: {},
    sidebarCollapsed: false,
    canvas: { artifact: null, width: 560 },

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
      // fresh chat — inside a project when one is given, standalone otherwise
      const key = newSessionKey()
      set((s) => ({
        currentSessionKey: key,
        currentProjectId: projectId || '',
        view: 'chat',
        openTabs: s.openTabs.some((t) => t.id === key)
          ? s.openTabs
          : [...s.openTabs, { id: key, agentId: s.currentAgentId }]
      }))
    },

    async renameSession(sessionId, title) {
      // optimistic: update the row (and the tab-title cache) now; the server
      // confirms via sessions.changed
      set((state) => ({
        sessionRows: state.sessionRows.map((row) =>
          row.sessionId === sessionId ? { ...row, title, titleManual: !!title.trim() } : row
        ),
        tabTitles: title.trim()
          ? { ...state.tabTitles, [sessionId]: title }
          : state.tabTitles
      }))
      try {
        await gateway.request('sessions.rename', {
          sessionKey: sessionId,
          agentId: get().currentAgentId || undefined,
          title
        })
      } catch {
        void refreshSessions() // failed — reload the truth
      }
    },

    async deleteSession(sessionId) {
      // optimistic removal; the server broadcasts sessions.changed as confirmation
      set((state) => {
        const sessions = { ...state.sessions }
        delete sessions[sessionId]
        return {
          sessionRows: state.sessionRows.filter((row) => row.sessionId !== sessionId),
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
          agentId: get().currentAgentId || undefined
        })
      } catch {
        void refreshSessions() // failed (e.g. active run) — reload the truth
      }
    },

    async moveSession(sessionId, projectId) {
      // optimistic re-group; server confirms + rebroadcasts via sessions.changed
      set((state) => ({
        sessionRows: state.sessionRows.map((row) =>
          row.sessionId === sessionId ? { ...row, projectId } : row
        )
      }))
      try {
        await gateway.request('sessions.move', {
          sessionKey: sessionId,
          agentId: get().currentAgentId || undefined,
          projectId
        })
      } catch {
        void refreshSessions()
      }
    },

    async duplicateSession(sessionId) {
      // the copy lands via the server's sessions.changed broadcast (refreshSessions)
      try {
        await gateway.request('sessions.duplicate', {
          sessionKey: sessionId,
          agentId: get().currentAgentId || undefined
        })
      } catch {
        void refreshSessions()
      }
    },

    async exportSessionMd(sessionId) {
      const row = get().sessionRows.find((r) => r.sessionId === sessionId)
      const title = row?.title || sessionId
      try {
        const payload = await gateway.request<{ messages: any[] }>('sessions.history', {
          sessionKey: sessionId,
          agentId: get().currentAgentId || undefined
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

    async resumeSession(sessionId) {
      const row = get().sessionRows.find((r) => r.sessionId === sessionId)
      set((state) => ({
        currentSessionKey: sessionId,
        currentProjectId: row?.projectId || '',
        view: 'chat',
        openTabs: state.openTabs.some((t) => t.id === sessionId)
          ? state.openTabs
          : [...state.openTabs, { id: sessionId, agentId: state.currentAgentId }]
      }))
      // already have this session in memory with content (it's live, or we loaded it
      // before) — don't clobber it by reloading.
      const existing = get().sessions[sessionId]
      if (existing && existing.items.length > 0) return
      try {
        const payload = await gateway.request<{ messages: any[] }>('sessions.history', {
          sessionKey: sessionId,
          agentId: get().currentAgentId || undefined
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
