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

import { useEffect, useMemo, useRef } from 'react'
import {
  ArrowRight,
  ArrowUpRight,
  Boxes,
  Gauge,
  PanelLeft,
  PanelRight,
  Plug,
  Sparkles,
  Workflow as WorkflowIcon,
} from 'lucide-react'

import { AGENT_ID, useClient } from './agentd/client'
import { useCredits } from './agentd/credits'
import { handleRunEvent } from './agentd/run-events'
import { MAX_FILES } from './agentd/chat'
import { useRun } from './agentd/run'
import { listSessions, loadHistory } from './agentd/sessions'
import { useApp, useSession } from './state/store'

import { Composer } from './components/Composer'
import { ReferenceMedia } from './components/ReferenceMedia'
import { Sidebar } from './components/Sidebar'
import { Thread } from './components/Thread'

/* THIS AGENT'S OWN SCREEN, in place of the scaffold's sample widgets. It reads the artifacts the
   runs really declared, so an empty shelf is a fact about the agent rather than a sign that
   nobody finished the window. */
import WorkflowShelf, { collectWorkflows } from './components/workflows/WorkflowShelf'
import { StudioDashboard } from './components/studio/StudioDashboard'
import type { Artifact } from './agentd/artifacts'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'
import { Settings } from './common/settings/Settings'

/* WHAT THIS AGENT IS, in the user's words rather than yours — the opening screen's whole job.
   Edit these four lines and the four cards below; they are the first thing anyone reads, and the
   default text says nothing because only you know what this agent is for. */
const AGENT_NAME = 'Comfy Artchitect'
const OPENING_EYEBROW = 'Point me at your ComfyUI'
const OPENING_HEADLINE = 'What should we build?'
const OPENING_BLURB =
  'I read what is actually installed on your instance, design the graph with you, run it there, ' +
  'and repair what the server rejects. I never name a model or a node I have not seen on your ' +
  'box — which is what makes the workflows I hand back ones that run.'

/* The four ways in. Each seeds the composer rather than sending, so the user can edit the
   suggestion before committing to it — the same reason the edit action exists on a sent turn. */
const OPENINGS: { icon: JSX.Element; title: string; sub: string; prompt: string }[] = [
  {
    icon: <Plug size={15} strokeWidth={1.7} />,
    title: 'Check the connection',
    sub: 'Reach it, and read the hardware',
    prompt: 'Connect to my ComfyUI and tell me what you can reach.',
  },
  {
    icon: <Boxes size={15} strokeWidth={1.7} />,
    title: 'See what is installed',
    sub: 'Models, LoRAs, custom nodes',
    prompt: 'List what is installed on my instance — checkpoints, LoRAs and custom nodes.',
  },
  {
    icon: <Sparkles size={15} strokeWidth={1.7} />,
    title: 'Build a workflow',
    sub: 'Designed around your models',
    prompt:
      'Build a text-to-image workflow using what my instance already has. Ask me whatever you need to know first.',
  },
  {
    icon: <Gauge size={15} strokeWidth={1.7} />,
    title: 'Make one faster',
    sub: 'Without changing the look',
    prompt:
      'Take my last workflow and make it faster without changing the look. Tell me the tradeoff before you change anything.',
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
  const sessions = useApp((s) => s.sessions)

  const { send, abort, addFiles, removeFile, sendReferences } = useRun(client)

  /* EVERY FILE THIS AGENT HAS WRITTEN, across every conversation in this window.
     Artifacts hang off the turn that produced them, which is right for the transcript and wrong
     for a shelf: a workflow built over six turns is findable only by scrolling. Gathering them
     here costs one pass and gives both screens the same source, so the count in the aside can
     never disagree with the number of cards on the shelf. */
  const artifacts = useMemo<Artifact[]>(
    () =>
      Object.values(sessions).flatMap((s) =>
        s.items.flatMap((i) => ('artifacts' in i && i.artifacts ? i.artifacts : [])),
      ),
    [sessions],
  )
  /* The newest emitted workflow's API file — the conversation header's subtitle, so the run
     the studio is about is named right over the transcript. */
  const latestWorkflow = useMemo(() => {
    const wf = collectWorkflows(artifacts)[0]
    return wf?.api?.name || wf?.ui?.name || ''
  }, [artifacts])

  const chatSide = useApp((s) => s.chatSide)
  const setChatSide = useApp((s) => s.setChatSide)

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

  /* RESUME A SAVED CHAT. Clicking a Recent row switches `currentKey` to a saved session that
     `openSession` seeded EMPTY (it must not clobber a live run). Here is where its transcript
     actually loads: a known-saved session with no items and nothing running gets its history
     fetched once and dropped in. The guards keep this off a brand-new chat (not in `chats`),
     a chat already populated, and a running one. */
  const historyTried = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!connected || !client || !currentKey) return
    const cur = sessions[currentKey]
    const isSaved = chats.some((c) => c.sessionId === currentKey)
    if (!isSaved || !cur || cur.items.length > 0 || cur.running) return
    if (historyTried.current.has(currentKey)) return
    historyTried.current.add(currentKey)
    void loadHistory(client, currentKey).then((items) => {
      if (!items.length) return
      // Only fill if still empty — a run may have started streaming while the fetch was in
      // flight, and the live items win. `patch`, not `openSession`: the session already exists
      // (seeded empty), and openSession deliberately never overwrites an existing one's items.
      const now = useApp.getState().sessions[currentKey]
      if (now && now.items.length === 0) useApp.getState().patch(currentKey, { items })
    })
  }, [connected, client, currentKey, chats, sessions])

  /* THE CARD OVER EVERYTHING, when the account menu asks to sign in. `<Gate>` in main.tsx
     handles the case where the daemon DEMANDS an account before the app runs; this is the other
     one — somebody choosing to sign in from inside a window that was working fine without it. */
  if (account.wantsSignIn) return <SignIn product={AGENT_NAME} onDone={account.signedIn} />

  /* WHAT THIS SCREEN IS ABOUT, from what is actually on it. A title invented from sample text
     would be a lie the first time somebody opened a real conversation. */
  const openChat = chats.find((c) => c.sessionId === currentKey)
  const empty = session.items.length === 0
  /* `pct` ARRIVES AS A FRACTION (0-1), not a percentage — the daemon sends `used / limit`
     rounded to 4 places. Rounding it straight to an integer floored every real conversation to
     "0% ctx" (anything under half a window), which read as a broken meter rather than a wrong
     unit. Scale here; the wire format is what agent-builder's ring already consumes. */
  const pct =
    session.usage && session.usage.limit > 0 ? Math.round(session.usage.pct * 100) : null

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
        /* A SCREEN OF THIS AGENT'S OWN, above the shared three. What this agent makes is FILES,
           and files are the one thing a conversation is a bad container for. */
        extraDestinations={[
          { id: 'workflows', label: 'Workflows', icon: <WorkflowIcon size={15} /> },
        ]}
      />

      {/* `is-studio` must track the SAME condition as the branch below — an unknown view falls
          through to the studio, and a modifier keyed to 'chat' alone would lay it out wrong. */}
      <main
        className={`main${
          ['credits', 'orgs', 'workflows', 'settings'].includes(view) ? '' : ' is-studio'
        }`}
      >
        {view === 'credits' ? (
          <Credits agentId={AGENT_ID} />
        ) : view === 'orgs' ? (
          <OrgView client={client ?? undefined} />
        ) : view === 'workflows' ? (
          <WorkflowShelf artifacts={artifacts} />
        ) : view === 'settings' ? (
          /* `agentId` is what makes this agent's values win over the daemon's, key by key. Pass
             `onRestart` too if your window can restart the daemon — some settings only take
             effect on a fresh process, and without it a save that needs one can only say so. */
          client && <Settings client={client} agentId={AGENT_ID} />
        ) : (
          /* THE STUDIO: conversation beside a live dashboard of what the run produced
             (design_handoff_agent_studio). The dashboard replaced the old stat aside — its
             KPI row carries the same numbers from the same sources. */
          <div className={`st-cols${chatSide === 'right' ? ' is-chat-right' : ''}`}>
            <div className="st-convo">
              <div className="st-convo-head">
                <span className="st-live-dot" />
                <div className="st-convo-titles">
                  <span className="st-convo-title">
                    {empty ? AGENT_NAME : openChat?.title || 'New conversation'}
                  </span>
                  {latestWorkflow && (
                    <span className="st-convo-sub st-mono">{latestWorkflow}</span>
                  )}
                </div>
                {pct !== null && <span className="st-ctx-pill st-mono">{pct}% ctx</span>}
                <button
                  className="st-swap"
                  title="Swap chat side"
                  onClick={() => setChatSide(chatSide === 'left' ? 'right' : 'left')}
                >
                  {chatSide === 'left' ? (
                    <PanelRight size={14} strokeWidth={1.7} />
                  ) : (
                    <PanelLeft size={14} strokeWidth={1.7} />
                  )}
                </button>
              </div>

              <div className="st-convo-body">
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
                  <Thread items={session.items} running={session.running} />
                )}
              </div>

              <div className="st-convo-foot">
                <ReferenceMedia
                  onReferences={(files) => sendReferences(files)}
                  disabled={!connected || session.running}
                />
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
            </div>

            <StudioDashboard
              client={client ?? undefined}
              connected={connected}
              running={session.running}
              artifacts={artifacts}
              credits={credits}
              onCredits={() => setView('credits')}
              onNewRun={() => seedComposer('Run the workflow again')}
              accountInitial={(account.auth?.email || '').slice(0, 1).toUpperCase()}
            />
          </div>
        )}
      </main>
    </div>
  )
}
