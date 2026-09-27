/* The question the chat asks before anything is rented: where should ComfyUI run?
 *
 * Shown above the message box while the account has not chosen (useComfyConnection: loaded, kind
 * null) — in every chat, because the choice is the account's and there is none yet. Answered
 * once, it is gone for good; the Workspace's Connection section changes it later.
 */

import type { AgentdClient } from '@agentd/client'

import { ConnectionSection } from './ConnectionSection'
import type { ComfyConnection } from './useComfyConnection'

export function ConnectionPrompt({ client, connection }: { client?: AgentdClient; connection: ComfyConnection }) {
  if (!connection.loaded || connection.kind) return null
  return (
    <section className="cn-prompt" aria-label="Where ComfyUI runs">
      <b className="cn-prompt-title">Where should Penguin run ComfyUI?</b>
      <p className="cn-note">
        Nothing is rented until you choose. Not sure? Rent a GPU — we start it and stop it for you.
      </p>
      <ConnectionSection client={client} connection={connection} />
    </section>
  )
}
