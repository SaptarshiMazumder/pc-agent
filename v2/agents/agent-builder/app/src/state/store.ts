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
 * IT OWNS THE CONVERSATIONS TOO, as of tabs. They were a hook holding one of everything, which is
 * the one shape that cannot answer for several at once — see ChatSession below.
 *
 * WHAT IT IS NOT: a mirror. Nothing here is copied from a hook by an effect; the store is the one
 * place `agents` and `chats` live.
 */

import type { AgentdClient } from '@agentd/client'
import { create } from 'zustand'

import { AGENT_ID } from '../agentd/client'
import {
  closeThinking,
  MAX_FILES,
  newSessionKey,
  preamble,
  readFile,
  restore,
  resultText,
  subjectOf,
  type SubagentItem,
  type ThreadItem,
} from '../agentd/chat'
import { freshArtifacts, readArtifacts, type Artifact } from '../agentd/artifacts'
import type { ContextUsage } from '../agentd/context-usage'
import { openable, type AgentRow } from '../agentd/roster'
import { forkSession, renameSession as rename, type ChatRow } from '../agentd/sessions'
import type { Attachment } from '@agentd/client'

/* `myagents` is being SUPERSEDED by `launchpad`, and both are here on purpose. The launchpad is
   the shelf plus the things you do next to it — start something, deal with what broke — so it is
   where the rail now points. The old route still renders, unreferenced, until the launchpad has
   grown the whole page: a view that is one commit from being needed again is cheaper to keep than
   to reconstruct. */
export type View = 'chat' | 'launchpad' | 'myagents' | 'settings' | 'credits' | 'orgs'

/** One conversation, keyed by session.
 *
 *  WHY IT IS A MAP NOW. The event handler used to drop any frame whose sessionKey was not the one
 *  on screen, which made a second open conversation impossible: switch away from a running chat
 *  and its deltas went in the bin, so switching back showed a stale transcript and an idle
 *  composer while the daemon was still working. Routing by key is what makes tabs real rather
 *  than cosmetic. */
export interface ChatSession {
  items: ThreadItem[]
  running: boolean
  pending: Attachment[]
  /** The agent this conversation is ABOUT — null while creating something new.
   *
   *  PER SESSION, not per window: the inspector shows the subject of the conversation you are
   *  reading, so switching tabs re-points it. Focus belongs to a conversation. */
  scope: AgentRow | null
  /** Has the scope preamble gone out? Carried into the FIRST message only — after that it is in
   *  the transcript and repeating it is noise. */
  scopeSent: boolean
  /** How full this conversation's context is, from the daemon's own measurement.
   *
   *  PER SESSION for the same reason everything else here is: a hook keyed to the open chat had
   *  to clear on every tab switch, so returning to a conversation showed an empty ring until its
   *  next reply — throwing away a number it had already been told. */
  usage: ContextUsage | null
  /** Files declared by tools during this run, waiting for an answer to hang under.
   *
   *  BUFFERED RATHER THAN ATTACHED ON ARRIVAL, because a tool announces a file the moment it
   *  writes one — usually before the assistant has said anything. Flushed at `agent_end` onto the
   *  last answer, or onto a bare one if the run produced files and no prose. */
  pendingArtifacts: Artifact[]
  /** Which workspace pane rides beside this conversation — '' is none (the chat at full
   *  width, exactly the old layout). PER SESSION like everything else here: switching tabs
   *  restores the pane that conversation was using, not the last one anyone used. */
  wsTab: '' | 'preview' | 'files' | 'caps' | 'test'
}

const blankSession = (): ChatSession => ({
  items: [],
  running: false,
  pending: [],
  scope: null,
  scopeSent: false,
  usage: null,
  pendingArtifacts: [],
  wsTab: '',
})

interface AppState {
  // ---- data ---------------------------------------------------------------
  agents: AgentRow[]
  chats: ChatRow[]
  // ---- conversations ------------------------------------------------------
  sessions: Record<string, ChatSession>
  /** Which conversation is on screen. */
  currentSessionKey: string
  /** The conversations with a tab, in strip order. A WORKING SET, not a list of what exists:
   *  closing a tab forgets the conversation here, and it stays in Recents to be reopened. */
  openTabs: string[]

  // ---- view state ---------------------------------------------------------
  view: View
  /** Which organization the orgs view is looking at — '' is the overview. Named exactly as
   *  agentd's store names these, because OrgView is agentd's file unchanged and reads both. */
  viewedOrgId: string
  viewOrg(orgId: string): void
  /** agentd's name for it, and agentd's meaning: collapsed to the icon rail, never gone. */
  sidebarCollapsed: boolean
  panelOpen: boolean
  /** Text to load into the composer, from a user message's Edit action.
   *
   *  AN OBJECT, NOT A STRING, and that is the whole trick: the composer reacts to this changing,
   *  and editing the same message twice in a row sets the same text. A bare string would compare
   *  equal, the effect would not re-run, and the second Edit would do nothing. */
  composerSeed: { text: string } | null

  // ---- actions ------------------------------------------------------------
  connect: (client: AgentdClient) => void
  disconnect: () => void
  reloadAgents: () => Promise<void>
  reloadChats: () => Promise<void>
  renameSession: (sessionKey: string, title: string) => Promise<void>
  duplicateSession: (sessionKey: string) => Promise<string>
  /** Delete a saved conversation — the row, its tab if open, and the transcript on the daemon. */
  deleteSession: (sessionKey: string) => Promise<void>
  setView: (view: View) => void
  toggleSidebar: () => void
  togglePanel: () => void
  seedComposer: (text: string) => void

  // ---- one conversation ---------------------------------------------------
  /** Start a fresh conversation, give it a tab, and make it current. Returns its key. */
  newSession: () => string
  /** Bring an already-open tab to the front. No reload: its transcript is still in memory, and
   *  re-reading it would throw away anything that streamed in while you were elsewhere. */
  activateTab: (key: string) => void
  closeTab: (key: string) => void
  /** Move `key` to where `before` currently sits. Drag-to-reorder; a no-op if either is gone. */
  reorderTabs: (key: string, before: string) => void
  closeOthers: (key: string) => void
  closeToLeft: (key: string) => void
  closeToRight: (key: string) => void
  closeAll: () => void
  /** Open a saved conversation, rendering its transcript and pointing the inspector at whatever
   *  it was about. Returns that agent's id, or '' when the transcript names none. */
  openSession: (key: string) => Promise<string>
  sendMessage: (text: string) => Promise<void>
  abortRun: () => Promise<void>
  addFiles: (list: FileList | File[] | null) => Promise<void>
  removeFile: (index: number) => void
  /** Point the current conversation at an agent, and tell the model so on the next message. */
  setScope: (agent: AgentRow | null) => void
  /** Switch the CURRENT conversation's workspace pane — '' closes it (full-width chat). */
  setWsTab: (tab: ChatSession['wsTab']) => void
  /** Bumped whenever a tool finishes in the CURRENT conversation — the inspector's file tree
   *  watches it. A counter rather than a callback: a store that calls back into its subscribers
   *  is a store that has to know who they are. */
  toolTick: number
}

/** The client, and the teardowns for its subscriptions. Module scope rather than store state:
 *  they are machinery, and putting them in the store would re-render every subscriber whenever a
 *  socket handler was replaced. */
let client: AgentdClient | null = null
let unsubscribe: Array<() => void> = []

/** Agent ids seen on the last roster load. NULL until the first one, so a cold start is not
 *  "everything is new" — the check below would then focus a random agent on boot. */
let seen: Set<string> | null = null

/** The conversation the window opens on. Made once at module load rather than in the initialiser,
 *  so a re-render cannot mint a second one. */
const FIRST_KEY = newSessionKey()

/** Patch one conversation by key. A no-op if it is gone — a tab closed mid-run still receives the
 *  events already in flight for it, and resurrecting it here would reopen something the user just
 *  closed. */
function patch(
  set: (fn: (s: AppState) => Partial<AppState>) => void,
  key: string,
  fn: (session: ChatSession) => Partial<ChatSession>,
): void {
  set((s) => {
    const session = s.sessions[key]
    if (!session) return {}
    return { sessions: { ...s.sessions, [key]: { ...session, ...fn(session) } } }
  })
}


/* ── run events ───────────────────────────────────────────────────────────────────────────────
 *
 * ROUTED BY SESSION KEY. This used to open with `if (payload.sessionKey !== current) return` — one
 * line, and the reason a second conversation could not exist: switch away from a running chat and
 * its deltas went in the bin, so coming back showed a stale transcript and an idle composer while
 * the daemon was still working. Every frame now finds its own conversation, whether or not you are
 * looking at it.
 */

/** Append to the bubble being streamed into, or start a new one.
 *
 *  A tool row (or anything else) landing in between ends the run of deltas, so the next one opens
 *  a fresh bubble BELOW it. That is the whole ordering rule. */
function appendTo(items: ThreadItem[], kind: 'bot' | 'think', delta: string, ts: number): ThreadItem[] {
  const next = closeThinking(items, kind === 'think')
  const last = next[next.length - 1]
  if (kind === 'bot') {
    if (last?.kind === 'bot' && last.streaming) {
      next[next.length - 1] = { ...last, text: last.text + delta }
    } else {
      next.push({ kind: 'bot', text: delta, streaming: true, ts })
    }
  } else if (last?.kind === 'think' && last.streaming) {
    next[next.length - 1] = { ...last, text: last.text + delta }
  } else {
    next.push({ kind: 'think', text: delta, streaming: true, ts })
  }
  return next
}

/** Commit whatever is streaming and drop the caret. Called whenever the assistant STOPS writing
 *  prose — a tool starting counts, and forgetting it left a blinking caret stranded on every
 *  bubble a tool call interrupted. It closes an open thinking block too. */
function settle(items: ThreadItem[]): ThreadItem[] {
  return closeThinking(items, false).map((it, i, all) =>
    i === all.length - 1 && it.kind === 'bot' && it.streaming ? { ...it, streaming: false } : it,
  )
}

/** Hang this run's files under its last answer, walking back from the end. A run that produced
 *  files and no prose gets a bare answer to carry them — otherwise the deliverable would exist
 *  with nowhere on screen to appear. Deduped against whatever that answer already holds. */
function attachArtifacts(items: ThreadItem[], artifacts: Artifact[], ts: number): ThreadItem[] {
  if (!artifacts.length) return items
  const next = [...items]
  for (let i = next.length - 1; i >= 0; i--) {
    const it = next[i]
    if (it.kind !== 'bot') continue
    const add = freshArtifacts(it.artifacts || [], artifacts)
    if (add.length) next[i] = { ...it, artifacts: [...(it.artifacts || []), ...add] }
    return next
  }
  next.push({ kind: 'bot', text: '', streaming: false, ts, artifacts })
  return next
}

function handleRunEvent(
  set: (fn: (s: AppState) => Partial<AppState>) => void,
  get: () => AppState,
  payload: any,
): void {
  const key = String(payload?.sessionKey || '')
  const ev = payload?.event
  if (!key || !ev || !get().sessions[key]) return

  /* THE SERVER'S CLOCK (`ts`, epoch SECONDS), so every client shows the same send time and it
     matches what the transcript stores. */
  const at = Number(payload?.ts)
  const ts = Number.isFinite(at) && at > 0 ? at * 1000 : Date.now()
  const on = (fn: (s: ChatSession) => Partial<ChatSession>) => patch(set, key, fn)

  switch (ev.type) {
    /* The daemon measures the context after every assistant message and says so. It is not
       decoration: a conversation that outgrows its model fails silently — the provider returns
       nothing, the retry re-sends, and the user sees "couldn't generate a response" twice with no
       cause on screen. */
    case 'context_usage': {
      on(() => ({
        usage: {
          used: Number(ev.used || 0),
          limit: Number(ev.limit || 0),
          pct: Number(ev.pct || 0),
          model: String(ev.model || ''),
          cached: Number(ev.cached || 0),
        },
      }))
      return
    }

    case 'message_update': {
      const kind = ev.kind === 'thinking_delta' ? 'think' : ev.kind === 'text_delta' ? 'bot' : null
      if (kind) on((s) => ({ items: appendTo(s.items, kind, String(ev.delta || ''), ts) }))
      return
    }

    /* A CHILD AGENT'S RUN, folded into one item. `start` opens it, `tool` appends a step,
       `done`/`error` closes it. Matched by child name against the still-running block, the way a
       tool row is matched by call id. */
    case 'subagent_event': {
      const agent = String(ev.childAgent || 'agent')
      const kind = String(ev.kind || '')
      const status = kind === 'error' ? 'error' : kind === 'done' ? 'done' : ('running' as const)
      on((s) => {
        const items = [...s.items]
        let at = -1
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'subagent' && it.agent === agent && it.status === 'running') {
            at = i
            break
          }
        }
        if (at < 0) {
          items.push({
            kind: 'subagent',
            agent,
            steps: kind === 'tool' && ev.tool ? [String(ev.tool)] : [],
            status,
            detail: ev.detail ? String(ev.detail) : undefined,
            ts,
          })
        } else {
          const prev = items[at] as SubagentItem
          items[at] = {
            ...prev,
            steps: kind === 'tool' && ev.tool ? [...prev.steps, String(ev.tool)] : prev.steps,
            status,
            detail: ev.detail ? String(ev.detail) : prev.detail,
          }
        }
        return { items }
      })
      return
    }

    /* What a long-running tool is doing WHILE it does it, rather than a spinner and nothing. */
    case 'tool_progress': {
      const id = String(ev.toolCallId || ev.toolName || '')
      const line = String(ev.message || ev.text || ev.detail || '').trim()
      if (!line) return
      on((s) => {
        const items = [...s.items]
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'tool' && it.id === id && !it.done) {
            items[i] = { ...it, progress: it.progress ? `${it.progress}\n${line}` : line }
            return { items }
          }
        }
        return {}
      })
      return
    }

    case 'tool_execution_start': {
      // The assistant stopped writing to start a tool. Settle the bubble (and lose the caret) —
      // the next delta opens a fresh one below the tool row.
      on((s) => ({
        items: [
          ...settle(s.items),
          {
            kind: 'tool',
            id: String(ev.toolCallId || ev.toolName || Math.random()),
            name: String(ev.toolName || '?'),
            // RAW. Flattening here is what made a tool row permanently unexpandable.
            args: (ev.args || {}) as Record<string, unknown>,
            result: '',
            done: false,
            isError: false,
            ts,
          },
        ],
      }))
      return
    }

    case 'tool_execution_end': {
      const id = String(ev.toolCallId || ev.toolName || '')
      on((s) => {
        const items = [...s.items]
        // Last match wins: a tool called twice in one turn has two rows with the same name, and
        // the running one is always the later.
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i]
          if (it.kind === 'tool' && it.id === id && !it.done) {
            items[i] = { ...it, done: true, isError: !!ev.isError, result: resultText(ev.result) }
            return { items }
          }
        }
        return {}
      })
      // What the tool produced, if anything. Deduped against what is already waiting.
      const made = readArtifacts(ev.artifacts)
      if (made.length) {
        on((s) => ({
          pendingArtifacts: [...s.pendingArtifacts, ...freshArtifacts(s.pendingArtifacts, made)],
        }))
      }
      /* EVERY tool, not a list of the ones that write — a list you must remember to extend is a
         bug waiting for the next tool. Only for the conversation ON SCREEN: the file tree shows
         the active subject, so a background tab's tool has nothing to refresh here. */
      if (key === get().currentSessionKey) set((s) => ({ toolTick: s.toolTick + 1 }))
      return
    }

    /* A run is MANY turns — the model answers, calls a tool, answers again. `turn_end` fires after
       each one, so it must only settle the current bubble; treating it as the end would flip the
       composer back to idle while the run is still going. */
    case 'message_end':
    case 'turn_end': {
      on((s) => ({ items: settle(s.items) }))
      return
    }

    // The configured model could not answer and another one took over. Never silent: "the model
    // you chose is not the one replying" is the fact that turns an unpaid API key from a mystery
    // into a one-line fix.
    case 'model_fallback': {
      on((s) => ({
        items: [
          ...settle(s.items),
          {
            kind: 'fallback',
            from: String(ev.from || '?'),
            to: String(ev.to || '?'),
            reason: String(ev.reason || '').slice(0, 120),
            ts,
          },
        ],
      }))
      return
    }

    /* `agent_end` is the run terminal (stopReason, and `error` when it failed).
     *
     * A FAILURE IS A SYSTEM NOTE, not an answer — the same shape agentd gives it. This used to
     * push a `bot` item reading "**Run failed.** …", which drew the failure as though the agent
     * had said it: a bubble, with a copy button, in the transcript's own voice. It is a fact
     * about the run, so it reads like the other facts about the run. */
    case 'agent_end': {
      on((s) => {
        const items = ev.error
          ? [
              ...settle(s.items),
              { kind: 'system' as const, tone: 'error' as const, text: String(ev.error), ts },
            ]
          : settle(s.items)
        return {
          running: false,
          items: attachArtifacts(items, s.pendingArtifacts, ts),
          pendingArtifacts: [],
        }
      })
      return
    }

    case 'error': {
      on((s) => ({
        running: false,
        items: [
          ...s.items,
          { kind: 'system', tone: 'error', text: String(ev.message || 'the run failed'), ts },
        ],
      }))
      return
    }
  }
}

export const useApp = create<AppState>()((set, get) => ({
  agents: [],
  chats: [],
  sessions: { [FIRST_KEY]: blankSession() },
  currentSessionKey: FIRST_KEY,
  openTabs: [FIRST_KEY],
  view: 'chat',
  viewedOrgId: '',
  sidebarCollapsed: false,
  panelOpen: true,
  composerSeed: null,

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
    /* A RECONNECT NO LONGER MEANS EVERY RUN IS DEAD. The daemon keeps a run alive when its
       window drops (detached; grace-reaped only if nobody returns), so each running session is
       ASKED about: `chat.status` answers AND re-attaches this window (cancelling the reaper).
       Still running — keep streaming on this socket. Ended — it finished or was reaped while we
       were away; the transcript holds whatever we missed. Older daemon: the old assumption. */
    for (const key of Object.keys(get().sessions)) {
      if (!get().sessions[key]?.running) continue
      void (async () => {
        let stillRunning = false
        try {
          const st = (await next.request('chat.status', { sessionKey: key })) as {
            running?: boolean
          }
          stillRunning = !!st?.running
        } catch {
          /* older daemon — no way to ask; assume the run is gone, as before */
        }
        if (stillRunning) return
        set((s) => {
          const session = s.sessions[key]
          if (!session?.running) return {}
          return {
            sessions: {
              ...s.sessions,
              [key]: {
                ...session,
                running: false,
                items: [
                  ...session.items,
                  {
                    kind: 'system' as const,
                    tone: 'error' as const,
                    text: 'This run ended while the window was away — the conversation up to here is saved. Reopen the chat to see anything you missed, or resend to continue.',
                    ts: Date.now(),
                  },
                ],
              },
            },
          }
        })
      })()
    }
    unsubscribe = [
      next.on('agents.changed', () => void get().reloadAgents()),
      next.on('sessions.changed', () => void get().reloadChats()),
      next.on('chat.event', (payload: any) => handleRunEvent(set, get, payload)),
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
        // Into the CONVERSATION that built it, not into a window-wide slot: an agent born from
        // this chat becomes this chat's subject, and a tab already working on something else
        // keeps its own.
        if (born) {
          const key = get().currentSessionKey
          patch(set, key, (session) => (session.scope ? {} : { scope: born }))
        }
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

  /* AGENTD'S SHAPE, kept: optimistic removal first, the RPC second, and on failure reload the
     truth rather than guessing at repair. The row disappears the moment you click because that
     is what you asked for; `sessions.changed` from the daemon is the confirmation, and a delete
     the daemon refused (an active run) comes back on the reload with nothing lost. */
  deleteSession: async (sessionKey) => {
    if (!client) throw new Error('not connected')
    set((s) => ({ chats: s.chats.filter((row) => row.sessionId !== sessionKey) }))
    if (get().openTabs.includes(sessionKey)) get().closeTab(sessionKey)
    try {
      await client.request('sessions.delete', { sessionKey })
    } catch {
      void get().reloadChats() // failed (e.g. active run) — reload the truth
    }
  },

  setView: (view) => set({ view }),
  viewOrg: (viewedOrgId) => set({ view: 'orgs', viewedOrgId }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  seedComposer: (text) => set({ composerSeed: { text } }),

  // ── conversations ─────────────────────────────────────────────────────────
  newSession: () => {
    const key = newSessionKey()
    set((s) => ({
      sessions: { ...s.sessions, [key]: blankSession() },
      openTabs: [...s.openTabs, key],
      currentSessionKey: key,
    }))
    return key
  },

  activateTab: (key) => {
    if (get().sessions[key]) set({ currentSessionKey: key })
  },

  /**
   * Close a tab and forget its conversation. The transcript is on disk — it stays in Recents and
   * reopens from there, so nothing is lost but the in-memory copy.
   *
   * A RUN IS NOT ABORTED by closing its tab. The daemon owns the run; closing a window onto it
   * says nothing about whether the work should stop, and silently killing a build because a tab
   * was tidied away would be the worse surprise. Frames still arriving for it are dropped by
   * `patch`, which no-ops on a session that is gone.
   *
   * FOCUS FALLS TO THE NEIGHBOUR at the same index — the tab that slid into the closed one's
   * place — which is where the eye already is. Closing the last one mints a fresh conversation
   * rather than leaving a window with nothing in it.
   */
  closeTab: (key) => {
    const { openTabs, currentSessionKey } = get()
    const at = openTabs.indexOf(key)
    if (at < 0) return
    const rest = openTabs.filter((k) => k !== key)
    set((s) => {
      const sessions = { ...s.sessions }
      delete sessions[key]
      return { sessions, openTabs: rest }
    })
    if (currentSessionKey !== key) return
    if (rest.length) set({ currentSessionKey: rest[Math.min(at, rest.length - 1)] })
    else get().newSession()
  },

  reorderTabs: (key, before) => {
    const tabs = get().openTabs
    const from = tabs.indexOf(key)
    const to = tabs.indexOf(before)
    if (from < 0 || to < 0 || from === to) return
    const next = [...tabs]
    next.splice(from, 1)
    next.splice(to, 0, key)
    set({ openTabs: next })
  },

  /* THE BULK CLOSERS all route through closeTab rather than rewriting openTabs themselves, so the
     rules that live there — forget the session, never abort its run, move focus to the neighbour,
     mint a fresh conversation when the last one goes — hold for every one of them. Four
     reimplementations of that is four places for it to drift. */
  closeOthers: (key) => {
    for (const k of get().openTabs.filter((t) => t !== key)) get().closeTab(k)
  },
  closeToLeft: (key) => {
    const tabs = get().openTabs
    const at = tabs.indexOf(key)
    if (at < 1) return
    for (const k of tabs.slice(0, at)) get().closeTab(k)
  },
  closeToRight: (key) => {
    const tabs = get().openTabs
    const at = tabs.indexOf(key)
    if (at < 0) return
    for (const k of tabs.slice(at + 1)) get().closeTab(k)
  },
  closeAll: () => {
    for (const k of [...get().openTabs]) get().closeTab(k)
  },

  /**
   * Open a saved conversation: render its transcript, then keep talking INTO it — the same key
   * goes back out on the next send, so the thread continues rather than forking.
   *
   * `scopeSent` starts TRUE: a resumed chat already carries its context in message one, so
   * re-sending the preamble would be repeating something the model can already read.
   */
  openSession: async (key) => {
    if (!client) throw new Error('not connected')
    // Placed before the await so the tab is current immediately; the transcript fills in when it
    // arrives. Without this a slow history load leaves the click looking ignored.
    const known = !!get().sessions[key]
    set((s) => ({
      sessions: { ...s.sessions, [key]: s.sessions[key] || { ...blankSession(), scopeSent: true } },
      openTabs: s.openTabs.includes(key) ? s.openTabs : [...s.openTabs, key],
      currentSessionKey: key,
    }))
    // ALREADY OPEN -> just show it. Re-reading the transcript would discard whatever streamed in
    // while this tab sat in the background, which is exactly what tabs exist to keep.
    if (known) return get().sessions[key]?.scope?.id || ''
    try {
      const res = await client.history(key, AGENT_ID)
      const messages = res?.messages || []
      patch(set, key, () => ({ items: restore(messages), running: false, pending: [] }))
      return subjectOf(messages)
    } catch (e) {
      patch(set, key, () => ({
        items: [
          {
            kind: 'bot',
            text: `**Could not load this chat.** ${String((e as Error)?.message || e)}`,
            streaming: false,
          },
        ],
      }))
      return ''
    }
  },

  sendMessage: async (text) => {
    if (!client) throw new Error('not connected')
    const key = get().currentSessionKey
    const session = get().sessions[key]
    if (!session) return
    const body = text.trim()
    // a message may be attachments-only — the daemon accepts that, so don't require text
    if ((!body && !session.pending.length) || session.running) return

    const sending = session.pending
    const scope = session.scope
    const carry = !!scope && !session.scopeSent
    // Marked BEFORE the await, not after: everything up to the await runs synchronously, so a flag
    // set afterwards is still false for anything reaching sendMessage in the same tick, and the
    // preamble goes out twice. The catch below puts the debt back.
    patch(set, key, (s) => ({
      pending: [],
      running: true,
      scopeSent: carry ? true : s.scopeSent,
      items: [...s.items, { kind: 'user', text: body, files: sending, ts: Date.now() }],
    }))

    // THE SCOPE, ONCE. The other half of this used to be a new-agent instruction that rode
    // every message until create_agent ran; the agent is created from the start dialog now, so
    // there is never a message about an agent that does not exist.
    const context = [carry && scope ? preamble(scope) : ''].filter(Boolean)

    try {
      await client.send({
        sessionKey: key,
        // `message`, not `text` — chat.send reads params.message and rejects an empty one.
        message: context.length ? `${context.join('\n')}\n\n${body}` : body,
        ...(sending.length ? { attachments: sending } : {}),
      })
    } catch (e) {
      patch(set, key, (s) => ({
        running: false,
        scopeSent: carry ? false : s.scopeSent, // it never reached the daemon; the retry carries it
        items: [
          ...s.items,
          {
            kind: 'bot',
            text: `**Could not send.** ${String((e as Error)?.message || e)}`,
            streaming: false,
          },
        ],
      }))
    }
  },

  abortRun: async () => {
    if (!client) return
    try {
      await client.abort(get().currentSessionKey)
    } catch {
      // the run may have just ended on its own — there is nothing to report to the user here
    }
  },

  addFiles: async (list) => {
    const files = Array.from(list || [])
    if (!files.length) return
    const key = get().currentSessionKey
    const read = await Promise.all(files.map(readFile))
    patch(set, key, (s) => ({ pending: [...s.pending, ...read].slice(0, MAX_FILES) }))
  },

  removeFile: (index) =>
    patch(set, get().currentSessionKey, (s) => ({
      pending: s.pending.filter((_, i) => i !== index),
    })),

  setScope: (agent) =>
    patch(set, get().currentSessionKey, () => ({ scope: agent, scopeSent: false })),

  setWsTab: (tab) => patch(set, get().currentSessionKey, () => ({ wsTab: tab })),

  toolTick: 0,
}))

/** The conversation on screen, or a blank one before the first has been made. */
export const useSession = (): ChatSession =>
  useApp((s) => s.sessions[s.currentSessionKey]) ?? EMPTY

/** What the inspector points at: the subject of the conversation you are reading. */
export const useSubject = (): AgentRow | null =>
  useApp((s) => s.sessions[s.currentSessionKey]?.scope ?? null)

/** A stable empty session, so `useSession` never returns a fresh object and re-renders forever. */
const EMPTY: ChatSession = Object.freeze(blankSession()) as ChatSession
