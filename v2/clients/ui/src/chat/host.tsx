/**
 * ChatHost — what a rendered conversation needs from whoever is hosting it.
 *
 * The twin of canvas/host.tsx, and it exists for the same reason: the message components are now
 * rendered by TWO clients. The shell feeds them from its store (ShellChatHost); an agent app feeds
 * them from an SDK socket (@agentd/canvas `mountChat`). Neither may be assumed by MessageItem, or
 * it stops being one implementation.
 *
 * DELIBERATELY THREE THINGS. A seam is only worth its indirection while it stays the short list of
 * what the components genuinely cannot answer themselves — everything else about a message is in
 * the item they are handed. Adding a fourth is a decision, not a formality: the shell has pages,
 * tabs, projects and a marketplace that an agent app has no equivalent for, and any of those
 * leaking in here would make the components unrenderable outside the shell again.
 */

import { createContext, useContext } from 'react'

export interface ChatHost {
  /** Is a run live in the conversation being rendered? (A plan's spinner animates only while it is.) */
  running: boolean
  /** Load text back into the composer — the Edit action on a user message. */
  seedComposer(text: string): void
  /** Open this tool's configuration surface. The shell has a settings page for it; a host with
   *  nowhere to send the user supplies a no-op and the gear simply does nothing. */
  openToolConfig(toolName: string): void
}

const Ctx = createContext<ChatHost | null>(null)

export const ChatHostProvider = Ctx.Provider

export function useChatHost(): ChatHost {
  const host = useContext(Ctx)
  if (!host) {
    // Loud and immediate: a thread rendered outside a provider would otherwise fail on the first
    // click with something unrelated-looking.
    throw new Error('ChatHost missing — wrap chat components in a <ChatHostProvider>')
  }
  return host
}
