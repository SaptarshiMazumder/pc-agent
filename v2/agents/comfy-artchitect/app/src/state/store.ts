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
import type { Attachment } from '@agentd/client'
import type { ChatRow } from '../agentd/sessions'

/** Which screen the main area is showing. Four of these are the shared modules; `chat` is the
 *  agent's own — and the open string tail is how a TEMPLATE adds its own view ('dashboard')
 *  without editing this file: a view is a string and a branch in App.tsx, nothing more. */
export type View = 'chat' | 'credits' | 'orgs' | 'settings' | (string & {})

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
}

const EMPTY: ChatSession = {
  items: [],
  running: false,
  pending: [],
  usage: null,
  pendingArtifacts: [],
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
  composerSeed: { text: string } | null
  seedComposer: (text: string | null) => void
}

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
      sessions: s.sessions[key] ? s.sessions : { ...s.sessions, [key]: { ...EMPTY, items } },
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
