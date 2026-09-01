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
 *
 * THE CONVERSATION HAS TWO FACES. Empty, it is an OPENING: what this agent is for, and four ways
 * in. In use, it is the transcript in a card with the run's own numbers beside it. They are the
 * same view — an agent that greets you and then throws the greeting away has told you what it
 * does exactly once, at the moment you had not yet asked.
 */

import { useEffect } from 'react'
import { ArrowRight, ArrowUpRight, Camera, Clock, Link2, Sparkles } from 'lucide-react'

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

/* WHAT THIS AGENT IS, in the user's words rather than yours — the opening screen's whole job.
   Edit these four lines and the four cards below; they are the first thing anyone reads, and the
   default text says nothing because only you know what this agent is for. */
const AGENT_NAME = 'This agent'
const OPENING_EYEBROW = 'Ready when you are'
const OPENING_HEADLINE = 'What can I help you with?'
const OPENING_BLURB =
  'Ask in plain language. I read what you paste, work with the files you drop in, and say what I ' +
  'cannot do rather than guessing.'

/* The four ways in. Each seeds the composer rather than sending, so the user can edit the
   suggestion before committing to it — the same reason the edit action exists on a sent turn. */
const OPENINGS: { icon: JSX.Element; title: string; sub: string; prompt: string }[] = [
  {
    icon: <Sparkles size={15} strokeWidth={1.7} />,
    title: 'Start something',
    sub: 'Describe what you want',
    prompt: 'I want to ',
  },
  {
    icon: <Link2 size={15} strokeWidth={1.7} />,
    title: 'Work from a link',
    sub: 'A page, a doc, a video',
    prompt: 'Read this and summarise what matters: ',
  },
  {
    icon: <Camera size={15} strokeWidth={1.7} />,
    title: 'Use a screenshot',
    sub: 'Drop it anywhere',
    prompt: 'Look at this screenshot and tell me what is wrong: ',
  },
  {
    icon: <Clock size={15} strokeWidth={1.7} />,
    title: 'Pick up where we left off',
    sub: 'Recent conversations in the rail',
    prompt: 'Continue from where we stopped: ',
  },
]

export default function App() {
  const { client, status } = useClient()
  const connected = status === 'open'

  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const newSession = useApp((s) => s.newSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  const chats = useApp((s) => s.chats)
  const seedComposer = useApp((s) => s.seedComposer)
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

  /* A RECONNECT NO LONGER MEANS THE RUN IS DEAD. The daemon keeps a run alive when its window
     drops (detached; reaped only if nobody returns within the grace period) — so the honest move
     is to ASK. `chat.status` answers AND re-attaches this window, cancelling the reaper: still
     running means keep streaming on this socket; ended means it finished (or was reaped) while
     we were away, and the transcript holds anything we missed. An older daemon without
     chat.status gets the old assumption. */
  useEffect(() => {
    if (!connected || !client) return
    const { sessions } = useApp.getState()
    for (const key of Object.keys(sessions)) {
      if (!sessions[key].running) continue
      void (async () => {
        let running = false
        try {
          const st = (await client.request('chat.status', { sessionKey: key })) as {
            running?: boolean
          }
          running = !!st?.running
        } catch {
          /* older daemon — no way to ask; assume the run is gone, as before */
        }
        if (running) return
        const { sessions: now, patch, append } = useApp.getState()
        if (!now[key]?.running) return
        patch(key, { running: false })
        append(key, [
          {
            kind: 'system',
            tone: 'error',
            text: 'This run ended while the window was away — the conversation up to here is saved. Reopen the chat to see anything you missed, or resend to continue.',
            ts: Date.now(),
          },
        ])
      })()
    }
  }, [connected, client])

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
  if (account.wantsSignIn) return <SignIn product={AGENT_NAME} onDone={account.signedIn} />

  /* WHAT THIS SCREEN IS ABOUT, from what is actually on it. A title invented from sample text
     would be a lie the first time somebody opened a real conversation. */
  const openChat = chats.find((c) => c.sessionId === currentKey)
  const turns = session.items.filter((i) => i.kind === 'user').length
  const empty = session.items.length === 0
  const pct = session.usage && session.usage.limit > 0 ? Math.round(session.usage.pct) : null

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
        client={client ?? undefined}
        status={status}
        name={AGENT_NAME}
        counts={{ credits: credits === null ? undefined : credits.toLocaleString() }}
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
            <header className="page-head">
              <div className="page-head-text">
                <h1 className="page-title">
                  {empty ? AGENT_NAME : openChat?.title || 'New conversation'}
                </h1>
                <p className="page-sub">
                  {chats.length > 0 && (
                    <>
                      {chats.length} conversation{chats.length === 1 ? '' : 's'}
                      {turns > 0 ? ' · ' : ''}
                    </>
                  )}
                  {turns > 0 && `${turns} turn${turns === 1 ? '' : 's'} here`}
                </p>
              </div>
            </header>

            <div className="stage">
              <div className="stage-main">
                {empty ? (
                  /* THE OPENING. Not a placeholder — the only screen guaranteed to be read. */
                  <div className="opening">
                    <span className="opening-eyebrow">
                      <ArrowRight size={13} strokeWidth={2} />
                      {OPENING_EYEBROW}
                    </span>
                    <h2 className="opening-headline">{OPENING_HEADLINE}</h2>
                    <p className="opening-blurb">{OPENING_BLURB}</p>
                    <div className="opening-grid">
                      {OPENINGS.map((o) => (
                        <button
                          key={o.title}
                          className="opening-card"
                          onClick={() => seedComposer(o.prompt)}
                        >
                          <span className="opening-card-ico">{o.icon}</span>
                          <span className="opening-card-text">
                            <span className="opening-card-title">{o.title}</span>
                            <span className="opening-card-sub">{o.sub}</span>
                          </span>
                          <ArrowUpRight className="opening-card-go" size={15} strokeWidth={1.7} />
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  /* THE TRANSCRIPT, in a card of its own so the conversation has an edge and the
                     page around it can carry the numbers without the two running together. */
                  <div className="convo-card">
                    <Thread items={session.items} running={session.running} />
                  </div>
                )}

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
                  /* HOW FULL THE CONTEXT IS. It reads as a line here and as a card in the aside;
                     both come from the same number, so they cannot disagree. */
                  meter={
                    pct === null ? null : (
                      <span
                        className="meter"
                        title={`${session.usage!.used} of ${session.usage!.limit} tokens`}
                      >
                        {pct}% context
                      </span>
                    )
                  }
                />
              </div>

              {/* THE RUN'S OWN NUMBERS, beside the run. Only what this window actually knows —
                  an aside of invented figures is worse than no aside. It folds away under a
                  narrow window (see styles.css) rather than squeezing the conversation. */}
              <aside className="stage-aside">
                {pct !== null && (
                  <div className="stat-card">
                    <span className="stat-head">
                      <Clock size={13} strokeWidth={1.7} />
                      Context used
                    </span>
                    <span className="stat-figure">
                      {pct}
                      <span className="stat-unit">%</span>
                    </span>
                    {/* The VALUE travels as a custom property, not as a width. A percentage is
                        data; how it is drawn — height, colour, easing — stays in the stylesheet
                        where a theme can reach it. */}
                    <span className="stat-bar">
                      <span
                        className="stat-bar-fill"
                        style={{ '--pct': pct } as React.CSSProperties}
                      />
                    </span>
                  </div>
                )}
                {credits !== null && (
                  <div className="stat-card">
                    <span className="stat-head">
                      <Sparkles size={13} strokeWidth={1.7} />
                      Credits
                    </span>
                    <span className="stat-figure">{credits.toLocaleString()}</span>
                    <button className="stat-link" onClick={() => setView('credits')}>
                      Buy more
                    </button>
                  </div>
                )}
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
