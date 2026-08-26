/* The window: a rail on the left, one screen on the right.
 *
 * THIS FILE IS YOURS. Everything here is a starting point you are meant to rewrite — the views,
 * the layout, what the chat is for. What must NOT be rewritten is anything under `src/common/`:
 * sign-in, credits, settings and organizations are the same four screens in every agent on this
 * platform, and `validate_agent` compares your copies against the source.
 *
 * FOUR OF THE FIVE VIEWS ARE ALREADY BUILT. `chat` is the one that is about your agent; the other
 * four arrived working and wired. Deleting one is not a saving — the validator refuses to package
 * an agent that cannot sign its user in, take a payment, be configured, or be shared with a
 * colleague, because each of those failures is silent and total once the agent is installed.
 *
 * A VIEW REPLACES THE WHOLE MAIN AREA rather than stacking under the chat's header. Rendering two
 * at once is how you end up with a conversation's toolbar sitting on top of a settings page.
 */

import { useEffect } from 'react'

import { AGENT_ID, useClient } from './agentd/client'
import { useCredits } from './agentd/credits'
import { handleRunEvent } from './agentd/run-events'
import { MAX_FILES } from './agentd/chat'
import { useRun } from './agentd/run'
import { listSessions } from './agentd/sessions'
import { useApp, useSession } from './state/store'

import { Composer } from './components/Composer'
import { Sidebar } from './components/Sidebar'
import { Thread } from './components/Thread'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'
import { Settings } from './common/settings/Settings'

export default function App() {
  const { client, status } = useClient()
  const connected = status === 'open'

  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const newSession = useApp((s) => s.newSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  const session = useSession()

  const { send, abort, addFiles, removeFile } = useRun(client)

  /* THE BALANCE, beside the thing that spends it. Re-read when a run ends, because that is when
     it changed. `null` means "not known" — a build with no accounts service, where showing a
     zero would be a lie. */
  const credits = useCredits(client!, session.running)

  /* ONE auth state for the window. It lives here rather than in the Sidebar because the sign-in
     card is rendered here too, and two `useAuth()` calls would be two states that disagree about
     whether the card is open. */
  const account = useAuth(client!)

  /* ONE SUBSCRIPTION, TORN DOWN ON RECONNECT. Signing in re-dials the socket, and without the
     cleanup each dial would stack another handler — every frame then folded twice, so a streamed
     answer arrived with every character doubled. */
  useEffect(() => {
    if (!client) return
    const off = client.on('chat.event', (payload: any) => handleRunEvent(payload))
    return () => off()
  }, [client])

  /* A RECONNECT MEANS EVERY IN-FLIGHT RUN IS DEAD. `running` only clears on `agent_end`, and a
     daemon that restarted mid-run never sends one — without this the composer says "running"
     forever for a run that no longer exists. */
  useEffect(() => {
    if (!connected) return
    const { sessions, patch, append } = useApp.getState()
    for (const key of Object.keys(sessions)) {
      if (!sessions[key].running) continue
      patch(key, { running: false })
      append(key, [
        {
          kind: 'system',
          tone: 'error',
          text: 'The daemon restarted mid-run, so this run is gone. Resend your last message to continue.',
          ts: Date.now(),
        },
      ])
    }
  }, [connected])

  /* A conversation to type into, and the list of the saved ones. Both wait for the socket: a
     window that lists nothing because it asked too early looks like a window with no history. */
  useEffect(() => {
    if (!connected || !client) return
    if (!currentKey) newSession(false) // a session to type into, NOT a view change
    void listSessions(client).then((rows) => useApp.getState().setChats(rows))
  }, [connected, client, currentKey, newSession])

  /* THE CARD OVER EVERYTHING, when the account menu asks to sign in. `<Gate>` in main.tsx
     handles the case where the daemon DEMANDS an account before the app runs; this is the other
     one — somebody choosing to sign in from inside a window that was working fine without it. */
  if (account.wantsSignIn) return <SignIn product="This agent" onDone={account.signedIn} />

  return (
    <div className="shell">
      {/* RELOADS THIS WINDOW when the agent is rebuilt, so building it stops meaning "reopen it
          by hand after every change". Renders nothing, and is inert once the agent is published —
          only the authoring plugin can emit the event it listens for. */}
      <LiveReload client={client ?? undefined} />
      <Sidebar
        view={view}
        onView={setView}
        onNewChat={() => newSession()}
        account={account}
        status={status}
      />

      <main className="main">
        {view === 'credits' ? (
          <Credits agentId={AGENT_ID} />
        ) : view === 'orgs' ? (
          <OrgView client={client ?? undefined} />
        ) : view === 'settings' ? (
          /* `agentId` is what makes this agent's values win over the daemon's, key by key. Pass
             `onRestart` too if your window can restart the daemon — some settings only take
             effect on a fresh process, and without it a save that needs one can only say so. */
          client && <Settings client={client} agentId={AGENT_ID} />
        ) : (
          <>
            <Thread items={session.items} running={session.running} />
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
              /* HOW FULL THE CONTEXT IS, as a line of text. A conversation that outgrows its
                 model fails silently — the provider returns nothing and the retry re-sends — so
                 the number is worth the corner it occupies. Replace it with a ring or a bar if
                 you would rather; the composer takes any node. */
              meter={
                session.usage && session.usage.limit > 0 ? (
                  <span className="meter" title={`${session.usage.used} of ${session.usage.limit} tokens`}>
                    {Math.round(session.usage.pct)}% context
                  </span>
                ) : null
              }
            />
          </>
        )}
      </main>
    </div>
  )
}
