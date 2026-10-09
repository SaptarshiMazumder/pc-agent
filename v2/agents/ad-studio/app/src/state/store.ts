/* The window's state, in one store.
 *
 * WHY A STORE AND NOT PROPS. A conversation's frames arrive from a socket, not from a click, so
 * whatever holds them has to be reachable from outside the tree. Threading that through props
 * meant every component between the socket and the message list carried arguments it did not use
 * — and the sidebar, which is nowhere near the chat, still needed to know whether a run was going.
 *
 * CONVERSATIONS ARE A MAP, keyed by session key. More than one can exist (you start a new chat,
 * the old one keeps its history), and a frame carries the key it belongs to — so a run that is
 * still finishing writes into ITS conversation rather than whichever one is on screen. `patch`
 * no-ops on a key that is gone, because a closed conversation can still receive in-flight frames
 * and that is not an error.
 *
 * YOURS TO CHANGE. This is the skeleton's shape, not a rule: add fields, add views, delete the
 * artifact list if the agent produces none. What must NOT be rebuilt is anything under
 * `src/common/` — see that folder's README.
 */

import { create } from 'zustand'

import { closeThinking, newSessionKey, type ThreadItem } from '../agentd/chat'
import type { Artifact } from '../agentd/artifacts'
import type { Selected } from '../agentd/campaigns'
import type { Attachment } from '@agentd/client'
import type { ChatRow } from '../agentd/sessions'

/** Which screen the main area is showing. Four of these are the shared modules; `chat` is the
 *  agent's own — and the open string tail is how a TEMPLATE adds its own view ('dashboard')
 *  without editing this file: a view is a string and a branch in App.tsx, nothing more. */
export type View = 'chat' | 'credits' | 'orgs' | 'settings' | (string & {})

/** One image or clip the full-screen viewer can show. */
export interface ViewerItem {
  src: string
  kind: 'image' | 'video'
  title: string
}

export interface StartChoice {
  recipe?: string
  cast?: string
  scene?: string
  mode?: 'ad' | 'post'
  collection?: string
}

/** How full the model's context window is, as the daemon last reported it. */
export interface ContextUsage {
  used: number
  limit: number
  /** How full, 0-100. Sent by the daemon rather than derived, so every client agrees. */
  pct: number
  model: string
  /** Of `used`, how much was served from the provider's prompt cache. */
  cached: number
}

export interface ChatSession {
  items: ThreadItem[]
  running: boolean
  /** Files chosen but not yet sent. Cleared by the send that carries them. */
  pending: Attachment[]
  usage: ContextUsage | null
  /** Files the agent wrote during the turn now in flight, waiting for a message to hang under. */
  pendingArtifacts: Artifact[]
  /** Tools still working in the background (a campaign step runs for minutes), by job id: the
   *  tool and its latest progress line. A job's result arrives later as a turn of its own. */
  jobs: Record<string, { tool: string; text: string }>
}

const EMPTY: ChatSession = {
  items: [],
  running: false,
  pending: [],
  usage: null,
  pendingArtifacts: [],
  jobs: {},
}

export interface AppState {
  view: View
  setView: (v: View) => void

  /** Every open conversation, by session key. */
  sessions: Record<string, ChatSession>
  currentSessionKey: string
  /** The sidebar's list of saved conversations. */
  chats: ChatRow[]
  setChats: (rows: ChatRow[]) => void

  openSession: (key: string, items?: ThreadItem[]) => void
  /** `show` decides whether the view switches to the chat. TRUE for a person clicking "New
   *  chat"; FALSE for boot, which needs a session to type into but must not decide what is on
   *  screen — a dashboard template opens on its dashboard, and the boot call was stomping that. */
  newSession: (show?: boolean) => string
  closeSession: (key: string) => void

  /** Merge fields into one conversation. Silently ignores a key that no longer exists. */
  patch: (key: string, fields: Partial<ChatSession>) => void
  /** Append items to one conversation, closing any open thinking block first. */
  append: (key: string, items: ThreadItem[], stillThinking?: boolean) => void
  /** Replace the last item of one conversation — how a streaming message grows. */
  replaceLast: (key: string, item: ThreadItem) => void

  /* Text put INTO the composer from somewhere else — a suggestion, or "edit and resend" on a
     message you already sent.
     AN OBJECT, not a bare string: editing the SAME message twice would otherwise set an identical
     value, the effect watching it would not re-run, and the second click would do nothing. */
  composerSeed: { text: string; append?: boolean } | null
  seedComposer: (text: string | null) => void
  /** Add text to what is already in the composer (a start choice after the first) — never
   *  replacing what the user typed. */
  appendComposer: (text: string) => void

  /** What a new chat's start has chosen so far — a recipe, a cast member ('new' = make one) —
   *  by session key, so the studio offers the OTHER choice next. Gone once the chat has a
   *  campaign (the studio then shows the campaign). */
  /** A new chat's choices so far. `mode` 'post': a chat making an Instagram post from a collection. */
  starts: Record<string, StartChoice>
  setStart: (session: string, choice: StartChoice) => void

  /** Bumped whenever something the studio shows may have changed — a tool finished, a job ended,
   *  a run ended. The studio re-reads the campaign on each bump instead of guessing which tool
   *  wrote what. */
  studioTick: number
  bumpStudio: () => void

  /** A campaign opened into a chat from the Campaigns page, by session key — for a campaign
   *  that chat did not start (the studio otherwise shows the chat's own newest campaign). */
  pinned: Record<string, string>
  pin: (session: string, campaign: string) => void
  /** A post opened from the Posts page into a chat that did not make it: session → post slug. */
  pinnedPost: Record<string, string>
  pinPost: (session: string, post: string) => void

  /** The chat column's width, px. Dragged wider only — the studio needs its own minimum. */
  chatWidth: number
  setChatWidth: (px: number) => void

  /** Images and clips shown full screen, over everything — the one opened, and the set it belongs
   *  to (a post's slides, a collection) to step through with the arrows. */
  viewer: { items: ViewerItem[]; at: number } | null
  openViewer: (v: ViewerItem, set?: ViewerItem[]) => void
  stepViewer: (by: -1 | 1) => void
  closeViewer: () => void

  /** What the user selected — stills and clips, from the board or Generations. Shown as chips
   *  above the composer; the next chat message carries their exact paths, then it clears. */
  selection: Selected[]
  toggleSelected: (item: Selected) => void
  /** Select `item` as the ONE image of its shot (the stills gate's pick is the selection). */
  selectOnly: (item: Selected) => void
  unselect: (path: string) => void
  clearSelection: () => void
}

export const CHAT_MIN_PX = 430
export const STUDIO_MIN_PX = 560

export const useApp = create<AppState>((set) => ({
  view: 'chat',
  setView: (view) => set({ view }),

  sessions: {},
  currentSessionKey: '',
  chats: [],
  setChats: (chats) => set({ chats }),

  openSession: (key, items = []) =>
    set((s) => ({
      currentSessionKey: key,
      view: 'chat',
      // AN EXISTING CONVERSATION IS NOT RESET by opening it again: its run may still be going.
      // BUT loaded history WINS over an empty placeholder: the rail opens a chat before the
      // transcript arrives, so the second call -- the one carrying the messages -- must be able
      // to fill it. Without this the fetched history was dropped on the floor and the thread
      // stayed blank, which is the bug the fetch was added to fix.
      sessions:
        s.sessions[key] && !(items.length && (s.sessions[key].items || []).length === 0)
          ? s.sessions
          : { ...s.sessions, [key]: { ...(s.sessions[key] || EMPTY), items } },
    })),

  newSession: (show = true) => {
    const key = newSessionKey()
    set((s) => ({
      currentSessionKey: key,
      view: show ? 'chat' : s.view,
      sessions: { ...s.sessions, [key]: { ...EMPTY } },
    }))
    return key
  },

  closeSession: (key) =>
    set((s) => {
      const sessions = { ...s.sessions }
      delete sessions[key]
      const rest = Object.keys(sessions)
      return {
        sessions,
        currentSessionKey: s.currentSessionKey === key ? rest[rest.length - 1] || '' : s.currentSessionKey,
      }
    }),

  patch: (key, fields) =>
    set((s) => {
      const cur = s.sessions[key]
      // NOT AN ERROR. A closed conversation can still receive frames from a run that was already
      // in flight; dropping them is the correct answer, and throwing here would take the window
      // down for something that happens in normal use.
      if (!cur) return s
      return { sessions: { ...s.sessions, [key]: { ...cur, ...fields } } }
    }),

  append: (key, items, stillThinking = false) =>
    set((s) => {
      const cur = s.sessions[key]
      if (!cur) return s
      return {
        sessions: {
          ...s.sessions,
          [key]: { ...cur, items: [...closeThinking(cur.items, stillThinking), ...items] },
        },
      }
    }),

  composerSeed: null,
  seedComposer: (text) => set({ composerSeed: text === null ? null : { text } }),
  appendComposer: (text) => set({ composerSeed: { text, append: true } }),

  starts: {},
  setStart: (session, choice) =>
    set((s) => ({ starts: { ...s.starts, [session]: { ...(s.starts[session] || {}), ...choice } } })),

  studioTick: 0,
  bumpStudio: () => set((s) => ({ studioTick: s.studioTick + 1 })),

  pinned: {},
  pin: (session, campaign) => set((s) => ({ pinned: { ...s.pinned, [session]: campaign } })),
  pinnedPost: {},
  pinPost: (session, post) => set((s) => ({ pinnedPost: { ...s.pinnedPost, [session]: post } })),

  chatWidth: CHAT_MIN_PX,
  setChatWidth: (px) => set({ chatWidth: Math.max(CHAT_MIN_PX, Math.round(px)) }),

  viewer: null,
  openViewer: (v, items) => {
    const all = items?.length ? items : [v]
    set({ viewer: { items: all, at: Math.max(0, all.findIndex((x) => x.src === v.src)) } })
  },
  stepViewer: (by) =>
    set((s) => (s.viewer ? { viewer: { ...s.viewer, at: (s.viewer.at + by + s.viewer.items.length) % s.viewer.items.length } } : {})),
  closeViewer: () => set({ viewer: null }),

  selection: [],
  toggleSelected: (item) =>
    set((s) => ({
      selection: s.selection.some((x) => x.path === item.path)
        ? s.selection.filter((x) => x.path !== item.path)
        : [...s.selection, item],
    })),
  selectOnly: (item) =>
    set((s) => ({
      selection: [
        ...s.selection.filter(
          (x) => !(x.kind === item.kind && x.campaign === item.campaign && x.shot === item.shot) && x.path !== item.path,
        ),
        item,
      ],
    })),
  unselect: (path) => set((s) => ({ selection: s.selection.filter((x) => x.path !== path) })),
  clearSelection: () => set({ selection: [] }),

  replaceLast: (key, item) =>
    set((s) => {
      const cur = s.sessions[key]
      if (!cur || !cur.items.length) return s
      const items = [...cur.items]
      items[items.length - 1] = item
      return { sessions: { ...s.sessions, [key]: { ...cur, items } } }
    }),
}))

/** The conversation on screen. Falls back to an empty one so the chat renders before the first
 *  session exists — a window that shows nothing until you click is a window that looks broken. */
export const useSession = (): ChatSession =>
  useApp((s) => s.sessions[s.currentSessionKey]) ?? EMPTY

export const useCurrentKey = (): string => useApp((s) => s.currentSessionKey)
