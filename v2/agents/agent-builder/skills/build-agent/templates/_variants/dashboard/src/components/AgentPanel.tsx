/* The agent, in a panel — always beside the work instead of being a separate screen.
 *
 * A TEMPLATE FILE. The thread rendering, the composer, the run plumbing and the store are all the
 * base skeleton's; this only arranges them into a column: a header, the conversation, and the
 * composer. The full-width chat view is gone from this template on purpose — a workbench where
 * the agent lives NEXT to the content never makes you choose between looking at the work and
 * talking about it.
 *
 * THE COMPOSER IS THE SKELETON'S OWN, IN FULL. This panel used to ship a cut-down one — a text
 * box and a send button — on the argument that attachments, credits and the context meter belong
 * to a wider screen. That argument was wrong in the way that matters: it meant a dashboard agent
 * could not be shown a screenshot, could not show its balance, and had no way to say the context
 * was nearly full. THE PANEL IS NARROW, NOT LESSER. Everything the chat template's composer does,
 * this does; `dashboard.css` does the fitting.
 *
 * It is also the portability rule this template rests on: `<Composer>` is imported from the
 * skeleton with zero edits, exactly like `<Thread>`. A component that had to be forked to be
 * narrower would be a component every future template forks again.
 */

import { MessageSquarePlus, Sparkles } from 'lucide-react'
import type { AgentdClient } from '@agentd/client'

import { MAX_FILES } from '../agentd/chat'
import { useCredits } from '../agentd/credits'
import { useRun } from '../agentd/run'
import { useApp, useSession } from '../state/store'
import { Composer } from './Composer'
import { Thread } from './Thread'

export default function AgentPanel({
  client,
  connected,
}: {
  client: AgentdClient | null
  connected: boolean
}) {
  const session = useSession()
  const newSession = useApp((s) => s.newSession)
  const setView = useApp((s) => s.setView)
  const { send, abort, addFiles, removeFile } = useRun(client)

  /* THE BALANCE, beside the thing that spends it — re-read when a run ends, because that is when
     it changed. `null` means "not known", which the composer renders as nothing rather than as a
     zero somebody would act on. */
  const credits = useCredits(client!, session.running)

  return (
    <aside className="agent-panel">
      <header className="agent-panel-head">
        <span className="agent-panel-title">Agent</span>
        {/* A fresh conversation WITHOUT leaving the screen — show=false keeps the view. */}
        <button
          className="dash-refresh"
          title="New conversation"
          aria-label="New conversation"
          onClick={() => newSession(false)}
        >
          <MessageSquarePlus size={15} strokeWidth={1.8} />
        </button>
      </header>

      <div className="agent-panel-thread">
        {/* WHAT THIS PANEL IS FOR, said once, in the space that would otherwise be blank. It is
            not a greeting — a panel that says "Hi!" has spent the only moment anyone reads it on
            nothing. Replace the line with what your agent can actually be asked here. */}
        {session.items.length === 0 && (
          <div className="agent-greeting">
            <span className="agent-greeting-ico">
              <Sparkles size={15} strokeWidth={1.9} />
            </span>
            <p>
              Ask about anything on this screen. I can explain a number, dig into where it came
              from, or make the change you decide on.
            </p>
          </div>
        )}
        <Thread items={session.items} running={session.running} />
      </div>

      <Composer
        running={session.running}
        pending={session.pending}
        onSend={(text) => void send(text)}
        onAbort={() => void abort()}
        onFiles={(files) => void addFiles(files)}
        onRemoveFile={removeFile}
        credits={credits}
        onCredits={() => setView('credits')}
        maxFiles={MAX_FILES}
        connected={connected}
        model={session.usage?.model || ''}
        placeholder="Ask about this screen…"
        /* The context meter, the same number the chat template shows — a conversation that
           outgrows its model fails silently, and a panel that stays open all day is exactly
           where a long one accumulates. */
        meter={
          session.usage && session.usage.limit > 0 ? (
            <span
              className="meter"
              title={`${session.usage.used} of ${session.usage.limit} tokens`}
            >
              {Math.round(session.usage.pct)}% context
            </span>
          ) : null
        }
      />
    </aside>
  )
}
