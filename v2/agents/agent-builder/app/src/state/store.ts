/* The sidebar's state, in one place — the shape agentd uses.
 *
 * WHY THIS EXISTS. agentd's sidebar and its parts read what they need straight from a store:
 * `Sidebar` pulls sixteen values, `SessionItem` another eight. This window threaded the same
 * facts down as twenty props on one component. Both work; only one of them lets the components be
 * COPIES. With props, the first twenty lines of every copied file are rewritten on arrival, and
 * from then on a diff between the two codebases is mostly wiring noise — so real drift, the kind
 * worth catching, hides in it. With the store the copies stay near line-for-line identical and a
 * difference means something.
 *
 * WHAT IT OWNS: the roster, the session list, and the sidebar's own view state. The daemon
 * subscriptions that keep the first two current moved here out of `useAgents`/`useSessions`,
 * unchanged — same requests, same events, same newborn rule, same swallowed failures.
 *
 * WHAT IT DELIBERATELY DOES NOT OWN: the conversation. `useChat` holds streaming state, the
 * pending queue and the transcript, and moving it is a change to how the chat BEHAVES rather than
 * to how the shell is wired. So the two actions that operate on a conversation — open this chat,
 * work on this agent — reach the sidebar as props from App, and everything else comes from here.
 *
 * WHAT IT IS NOT: a mirror. Nothing here is copied from a hook by an effect; the store is the one
 * place `agents` and `chats` live.
 */

import type { AgentdClient } from '@agentd/client'
import { create } from 'zustand'

import { AGENT_ID } from '../agentd/client'
import { openable, type AgentRow } from '../agentd/roster'
import { forkSession, renameSession as rename, type ChatRow } from '../agentd/sessions'

export type View = 'chat' | 'myagents'

interface AppState {
  // ---- data ---------------------------------------------------------------
  agents: AgentRow[]
  chats: ChatRow[]
  /** The agent this conversation is about. Null until one is chosen or built. */
  selected: AgentRow | null

  // ---- view state ---------------------------------------------------------
  view: View
  /** agentd's name for it, and agentd's meaning: collapsed to the icon rail, never gone. */
  sidebarCollapsed: boolean
  panelOpen: boolean

  // ---- actions ------------------------------------------------------------
  connect: (client: AgentdClient) => void
  disconnect: () => void
  reloadAgents: () => Promise<void>
  reloadChats: () => Promise<void>
  renameSession: (sessionKey: string, title: string) => Promise<void>
  duplicateSession: (sessionKey: string) => Promise<string>
  setView: (view: View) => void
  toggleSidebar: () => void
  togglePanel: () => void
  select: (agent: AgentRow | null) => void
}

/** The client, and the teardowns for its subscriptions. Module scope rather than store state:
 *  they are machinery, and putting them in the store would re-render every subscriber whenever a
 *  socket handler was replaced. */
let client: AgentdClient | null = null
let unsubscribe: Array<() => void> = []

/** Agent ids seen on the last roster load. NULL until the first one, so a cold start is not
 *  "everything is new" — the check below would then focus a random agent on boot. */
let seen: Set<string> | null = null

export const useApp = create<AppState>()((set, get) => ({
  agents: [],
  chats: [],
  selected: null,
  view: 'chat',
  sidebarCollapsed: false,
  panelOpen: true,

  /**
   * Attach to the socket: load both lists, then keep them current with no polling.
   *
   * IDEMPOTENT ON PURPOSE. App calls this on every transition to `open`, because signing in
   * re-dials the socket with a new session and the lists have to be re-read as the new identity.
   * Without the teardown at the top, each of those would stack another pair of handlers and the
   * roster would reload twice, then four times, then eight — the same bug `useDaemonEvent`'s
   * unsubscribe exists to prevent.
   */
  connect: (next) => {
    get().disconnect()
    client = next
    unsubscribe = [
      next.on('agents.changed', () => void get().reloadAgents()),
      next.on('sessions.changed', () => void get().reloadChats()),
    ]
    void get().reloadAgents()
    void get().reloadChats()
  },

  disconnect: () => {
    unsubscribe.forEach((off) => off())
    unsubscribe = []
  },

  /**
   * The roster.
   *
   * THE NEWBORN RULE, carried over exactly: when precisely one openable agent appears that was
   * not there before, it was just BUILT — in this window, by this conversation — so it takes
   * focus. Watching its files appear is what the inspector is for, and making the user go and
   * find what they just asked for is a strange thing to do to them. One, not "any": a batch that
   * arrives together is an install or a refresh, and picking one of those would be a guess.
   *
   * It never STEALS focus. `selected ?? born` means an agent already being worked on keeps the
   * panel, whatever else shows up while you are working.
   */
  reloadAgents: async () => {
    if (!client) return
    let rows: AgentRow[] = []
    try {
      const res = await client.agents()
      rows = (res?.agents as AgentRow[]) || []
    } catch {
      // The roster is chrome. A failure here leaves the list empty and the rest of the window
      // working; the connection status in the rail is what reports a daemon that is not there.
      rows = []
    }
    const ids = new Set(openable(rows).map((a) => a.id))
    if (seen) {
      const fresh = [...ids].filter((id) => !seen!.has(id))
      if (fresh.length === 1) {
        const born = rows.find((a) => a.id === fresh[0])
        if (born) set((s) => ({ selected: s.selected ?? born }))
      }
    }
    seen = ids
    set({ agents: rows })
  },

  reloadChats: async () => {
    if (!client) return
    try {
      const res = await client.sessions(AGENT_ID)
      set({ chats: (res?.sessions as ChatRow[]) || [] })
    } catch {
      // Advisory only — the chat itself works without its own history list.
      set({ chats: [] })
    }
  },

  /* Both of these are ADDRESSES, not implementations. The RPCs live in agentd/sessions.ts beside
     the row shape they operate on, so the ⋯ menu's Duplicate and the composer's Fork are two
     doors onto one piece of code rather than two copies of it that can drift. */
  renameSession: async (sessionKey, title) => {
    if (!client) throw new Error('not connected')
    await rename(client, sessionKey, title)
  },

  duplicateSession: async (sessionKey) => {
    if (!client) throw new Error('not connected')
    return forkSession(client, sessionKey)
  },

  setView: (view) => set({ view }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  select: (agent) => set({ selected: agent }),
}))
