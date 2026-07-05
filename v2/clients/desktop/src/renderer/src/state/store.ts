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
}

export type View = 'chat' | 'store' | 'settings'

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
  for (const message of messages) {
    const ts = toMs(message.ts)
    if (message.role === 'user') {
      items.push({ kind: 'user', text: String(message.content ?? ''), ts })
    } else if (message.role === 'assistant') {
      let lastTextIdx = -1
      for (const block of message.content || []) {
        if (block.type === 'text' && block.text) {
          lastTextIdx = items.length
          items.push({ kind: 'assistant', text: block.text, streaming: false, ts })
        } else if (block.type === 'thinking' && block.thinking) {
          items.push({ kind: 'thinking', text: block.thinking, streaming: false, ts })
        } else if (block.type === 'toolCall') {
          toolIndexByCallId.set(String(block.id), items.length)
          items.push({
            kind: 'tool',
            name: block.name || '?',
            args: block.arguments || {},
            result: '',
            isError: false,
            done: false,
            ts
          })
        }
      }
      // artifacts the assistant's text referenced attach to its final text bubble
      if (message.artifacts?.length && lastTextIdx >= 0) {
        items[lastTextIdx] = { ...items[lastTextIdx], artifacts: message.artifacts }
      }
      if (message.errorMessage) {
        items.push({ kind: 'system', tone: 'error', text: String(message.errorMessage), ts })
      }
    } else if (message.role === 'toolResult') {
      const text = (message.content || []).map((block: any) => block?.text || '').join('')
      const index = toolIndexByCallId.get(String(message.toolCallId))
      if (index !== undefined && items[index]?.kind === 'tool') {
        const tool = items[index] as Extract<ChatItem, { kind: 'tool' }>
        items[index] = { ...tool, result: text, isError: !!message.isError, done: true, artifacts: message.artifacts }
      } else {
        // orphan result (no matching call in this transcript) — show it anyway
        items.push({
          kind: 'tool',
          name: message.toolName || '?',
          args: {},
          result: text,
          isError: !!message.isError,
          done: true,
          ts,
          artifacts: message.artifacts
        })
      }
    }
  }
  return dedupeArtifacts(items)
}

/** Keep only the FIRST render of each artifact path across a transcript — the tool that
 *  produced a file usually shows it, and the assistant's summary re-mentions it. */
function dedupeArtifacts(items: ChatItem[]): ChatItem[] {
  const seen = new Set<string>()
  for (const it of items) {
    if (!it.artifacts?.length) continue
    const fresh = it.artifacts.filter((a) => !seen.has(a.path))
    fresh.forEach((a) => seen.add(a.path))
    it.artifacts = fresh.length ? fresh : undefined
  }
  return items
}

/** Incoming artifacts not already rendered anywhere in this live session (so a file
 *  shown by its producing tool isn't repeated when the assistant summarises). */
function newArtifacts(session: SessionState, incoming?: Artifact[]): Artifact[] | undefined {
  if (!incoming?.length) return undefined
  const seen = new Set<string>()
  for (const it of session.items) it.artifacts?.forEach((a) => seen.add(a.path))
  const fresh = incoming.filter((a) => !seen.has(a.path))
  return fresh.length ? fresh : undefined
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

  catalog: CatalogBundle[]
  catalogError: string
  installed: InstalledBundle[]
  installBusy: Record<string, string>

  notifications: NotificationRow[]

  bootstrap(): Promise<void>
  setView(view: View): void
  toggleTheme(): void
  toggleSidebar(): void
  activateTab(tab: OpenTab): Promise<void>
  closeTab(sessionId: string): void
  reorderTabs(from: string, to: string): void
  selectAgent(agentId: string): Promise<void>
  createAgent(fields: { name: string; description?: string; identity?: string }): Promise<string>
  newSession(projectId?: string): void
  resumeSession(sessionId: string): Promise<void>
  renameSession(sessionId: string, title: string): Promise<void>
  deleteSession(sessionId: string): Promise<void>
  createProject(name: string): Promise<void>
  renameProject(projectId: string, name: string): Promise<void>
  deleteProject(projectId: string): Promise<void>
  sendMessage(text: string): Promise<void>
  abortRun(): Promise<void>
  refreshCatalog(): Promise<void>
  installBundle(id: string): Promise<void>
  uninstallBundle(id: string): Promise<void>
}

export const useApp = create<AppState>((set, get) => {
  // ---- event plumbing (registered once at bootstrap) --------------------------

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
        patchSession(sessionKey, (session) => {
          const fresh = newArtifacts(session, event.artifacts)
          let attached = !fresh // nothing to attach -> treat as done
          const items = session.items.map((item) => {
            const stopped = 'streaming' in item && item.streaming ? { ...item, streaming: false } : item
            return stopped
          })
          // attach the turn's artifacts to its LAST assistant bubble (walk from the end)
          if (fresh) {
            for (let i = items.length - 1; i >= 0; i--) {
              if (items[i].kind === 'assistant') {
                items[i] = { ...items[i], artifacts: [...(items[i].artifacts || []), ...fresh] }
                attached = true
                break
              }
            }
            if (!attached) items.push({ kind: 'assistant', text: '', streaming: false, ts, artifacts: fresh })
          }
          return { ...session, items }
        })
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
          const fresh = newArtifacts(session, event.artifacts)
          for (let i = items.length - 1; i >= 0; i--) {
            const item = items[i]
            if (item.kind === 'tool' && !item.done && item.name === (event.toolName || '?')) {
              items[i] = { ...item, result: resultText(event.result), isError: !!event.isError, done: true, artifacts: fresh }
              break
            }
          }
          return { ...session, items }
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
        patchSession(sessionKey, (session) => ({
          items: error
            ? [...session.items, { kind: 'system', tone: 'error', text: error, ts }]
            : session.items.map((item) =>
                'streaming' in item && item.streaming ? { ...item, streaming: false } : item
              ),
          running: false
        }))
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
      const { openTabs, currentSessionKey } = get()
      const idx = openTabs.findIndex((t) => t.id === sessionId)
      const tabs = openTabs.filter((t) => t.id !== sessionId)
      set({ openTabs: tabs })
      if (currentSessionKey === sessionId) {
        const next = tabs[idx] || tabs[idx - 1] || tabs[tabs.length - 1]
        if (next) void get().activateTab(next)
        else get().newSession()
      }
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

    async sendMessage(text) {
      const { currentSessionKey, currentAgentId, currentProjectId } = get()
      patchSession(currentSessionKey, (session) => ({
        items: [...session.items, { kind: 'user', text, ts: Date.now() }],
        running: true
      }))
      try {
        await gateway.request('chat.send', {
          sessionKey: currentSessionKey,
          message: text,
          agentId: currentAgentId || undefined,
          projectId: currentProjectId || undefined,
          idempotencyKey: `${currentSessionKey}-${Date.now()}`
        })
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
