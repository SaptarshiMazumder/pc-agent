/**
 * App state (zustand) — the renderer's single source of truth.
 *
 * bootstrap(): flavor -> ensure daemon -> connect WS -> hello -> sessions ->
 * (Studio flavors) preinstall bundled .agentpkg files. Every broadcast event the
 * daemon emits lands here and mutates exactly one slice; components just render.
 */

import { create } from 'zustand'

import { gateway } from '../gateway/client'
import type {
  AgentEvent,
  AgentInfo,
  CatalogBundle,
  Hello,
  InstalledBundle,
  SessionRow
} from '../gateway/protocol'
import { resultText } from '../gateway/protocol'

export type ChatItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming: boolean }
  | { kind: 'thinking'; text: string; streaming: boolean }
  | { kind: 'tool'; name: string; args: Record<string, unknown>; result: string; isError: boolean; done: boolean }
  | { kind: 'system'; text: string; tone: 'info' | 'error' }

export interface SessionState {
  items: ChatItem[]
  running: boolean
}

export type View = 'chat' | 'store' | 'settings'

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

/** Rebuild a saved transcript (sessions.history message dicts) into the same
 *  ChatItem[] the live event path produces, so a resumed session renders identically:
 *  user text, assistant text/thinking, and tool calls merged with their results. */
function historyToItems(messages: any[]): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndexByCallId = new Map<string, number>()
  for (const message of messages) {
    if (message.role === 'user') {
      items.push({ kind: 'user', text: String(message.content ?? '') })
    } else if (message.role === 'assistant') {
      for (const block of message.content || []) {
        if (block.type === 'text' && block.text) {
          items.push({ kind: 'assistant', text: block.text, streaming: false })
        } else if (block.type === 'thinking' && block.thinking) {
          items.push({ kind: 'thinking', text: block.thinking, streaming: false })
        } else if (block.type === 'toolCall') {
          toolIndexByCallId.set(String(block.id), items.length)
          items.push({
            kind: 'tool',
            name: block.name || '?',
            args: block.arguments || {},
            result: '',
            isError: false,
            done: false
          })
        }
      }
      if (message.errorMessage) {
        items.push({ kind: 'system', tone: 'error', text: String(message.errorMessage) })
      }
    } else if (message.role === 'toolResult') {
      const text = (message.content || []).map((block: any) => block?.text || '').join('')
      const index = toolIndexByCallId.get(String(message.toolCallId))
      if (index !== undefined && items[index]?.kind === 'tool') {
        const tool = items[index] as Extract<ChatItem, { kind: 'tool' }>
        items[index] = { ...tool, result: text, isError: !!message.isError, done: true }
      } else {
        // orphan result (no matching call in this transcript) — show it anyway
        items.push({
          kind: 'tool',
          name: message.toolName || '?',
          args: {},
          result: text,
          isError: !!message.isError,
          done: true
        })
      }
    }
  }
  return items
}

interface AppState {
  flavor: FlavorInfo | null
  supervisor: SupervisorStatus
  connection: 'idle' | 'connecting' | 'open' | 'closed'
  hello: Hello | null
  view: View

  agents: AgentInfo[]
  currentAgentId: string
  sessionRows: SessionRow[]
  currentSessionKey: string
  sessions: Record<string, SessionState>

  catalog: CatalogBundle[]
  catalogError: string
  installed: InstalledBundle[]
  installBusy: Record<string, string>

  notifications: NotificationRow[]

  bootstrap(): Promise<void>
  setView(view: View): void
  selectAgent(agentId: string): Promise<void>
  newSession(): void
  resumeSession(sessionId: string): Promise<void>
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

  function appendStreaming(sessionKey: string, kind: 'assistant' | 'thinking', delta: string): void {
    patchSession(sessionKey, (session) => {
      const items = [...session.items]
      const last = items[items.length - 1]
      if (last && last.kind === kind && last.streaming) {
        items[items.length - 1] = { ...last, text: last.text + delta }
      } else {
        items.push({ kind, text: delta, streaming: true } as ChatItem)
      }
      return { ...session, items }
    })
  }

  function handleAgentEvent(sessionKey: string, event: AgentEvent): void {
    switch (event.type) {
      case 'message_update':
        if (event.kind === 'text_delta') appendStreaming(sessionKey, 'assistant', event.delta || '')
        else if (event.kind === 'thinking_delta') appendStreaming(sessionKey, 'thinking', event.delta || '')
        break
      case 'message_end':
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
            { kind: 'tool', name: event.toolName || '?', args: event.args || {}, result: '', isError: false, done: false }
          ]
        }))
        break
      case 'tool_execution_end':
        patchSession(sessionKey, (session) => {
          const items = [...session.items]
          for (let i = items.length - 1; i >= 0; i--) {
            const item = items[i]
            if (item.kind === 'tool' && !item.done && item.name === (event.toolName || '?')) {
              items[i] = { ...item, result: resultText(event.result), isError: !!event.isError, done: true }
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
                : `subagent ${event.childAgent} done`
            }
          ]
        }))
        break
      case 'agent_end': {
        const error = event.stopReason === 'error' ? String(event.error || 'run failed') : ''
        patchSession(sessionKey, (session) => ({
          items: error
            ? [...session.items, { kind: 'system', tone: 'error', text: error }]
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
    await refreshSessions()
    await preinstallBundles()
  }

  async function refreshSessions(): Promise<void> {
    const { currentAgentId } = get()
    try {
      const payload = await gateway.request<{ sessions: SessionRow[] }>('sessions.list', {
        agentId: currentAgentId
      })
      set({ sessionRows: payload.sessions || [] })
    } catch {
      set({ sessionRows: [] })
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
      handleAgentEvent(String(payload.sessionKey || ''), (payload.event || {}) as AgentEvent)
    })
    gateway.on('agents.changed', (payload) => {
      set({ agents: (payload.agents as AgentInfo[]) || [] })
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

    agents: [],
    currentAgentId: '',
    sessionRows: [],
    currentSessionKey: newSessionKey(),
    sessions: {},

    catalog: [],
    catalogError: '',
    installed: [],
    installBusy: {},

    notifications: [],

    async bootstrap() {
      const flavor = (await window.agentd.flavor()) as FlavorInfo
      set({ flavor })
      window.agentd.onSupervisorStatus((status) => set({ supervisor: status as SupervisorStatus }))
      set({ supervisor: (await window.agentd.supervisorStatus()) as SupervisorStatus })
      wireEvents()
      const { url } = await window.agentd.ensureDaemon()
      set({ connection: 'connecting' })
      gateway.connect(url)
    },

    setView(view) {
      set({ view })
      if (view === 'store') void get().refreshCatalog()
    },

    async selectAgent(agentId) {
      set({ currentAgentId: agentId, currentSessionKey: newSessionKey() })
      await refreshSessions()
    },

    newSession() {
      set({ currentSessionKey: newSessionKey() })
    },

    async resumeSession(sessionId) {
      set({ currentSessionKey: sessionId, view: 'chat' })
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
      const { currentSessionKey, currentAgentId } = get()
      patchSession(currentSessionKey, (session) => ({
        items: [...session.items, { kind: 'user', text }],
        running: true
      }))
      try {
        await gateway.request('chat.send', {
          sessionKey: currentSessionKey,
          message: text,
          agentId: currentAgentId || undefined,
          idempotencyKey: `${currentSessionKey}-${Date.now()}`
        })
      } catch (error) {
        patchSession(currentSessionKey, (session) => ({
          items: [
            ...session.items,
            { kind: 'system', tone: 'error', text: error instanceof Error ? error.message : String(error) }
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
