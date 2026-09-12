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

import { useCallback, useEffect, useMemo, useRef } from 'react'
import { onIdentityChanged } from '@agentd/client'
import {
  ArrowRight,
  ArrowUpRight,
  Boxes,
  Gauge,
  Loader2,
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
import { ChatResizer } from './components/studio/ChatResizer'
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

  /* THE FILES THIS CONVERSATION MADE — not every conversation's.
     Artifacts hang off the turn that produced them, which is right for the transcript and wrong
     for a rail: a workflow built over six turns is findable only by scrolling. Gathering them
     costs one pass and gives the rail and the shelf the same source, so a count can never
     disagree with what is listed.

     SCOPED TO THE OPEN CHAT, which it deliberately was not before. Flattening every session put
     one project's renders in another project's workspace, so the rail described the window rather
     than the thing being worked on — and switching chats changed nothing, which made the two
     panels look unrelated. A chat and its files are one subject. */
  const artifacts = useMemo<Artifact[]>(
    () =>
      (sessions[currentKey]?.items || []).flatMap((i) =>
        'artifacts' in i && i.artifacts ? i.artifacts : [],
      ),
    [sessions, currentKey],
  )
  /* The newest emitted workflow's API file — the conversation header's subtitle, so the run
     the studio is about is named right over the transcript. */
  const latestWorkflow = useMemo(() => {
    const wf = collectWorkflows(artifacts)[0]
    return wf?.api?.name || wf?.ui?.name || ''
  }, [artifacts])

  const chatSide = useApp((s) => s.chatSide)
  const chatWidth = useApp((s) => s.chatWidth)
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

  /* READING THE SAVED-CONVERSATION LIST — the one place that does it, so the rail's waiting and
     error states have a single owner. A rejection is reported rather than dropped: `listSessions`
     used to be a bare `.then`, which meant a failed read left an empty rail that looked exactly
     like an account with no history. */
  const refreshChats = useCallback(
    (forget = false) => {
      const { beginChatsLoad, setChats, failChatsLoad } = useApp.getState()
      beginChatsLoad(forget)
      /* NOT OVER A SOCKET THAT IS MID-REDIAL. Signing in re-dials the connection, so an identity
         change reaches us while it is between credentials — a read issued now either fails or
         answers for the account that has just left. Marking the list as loading and letting the
         effect below issue it when the socket is back is the same wait with the right answer at
         the end of it, instead of an error flashing up and correcting itself. */
      if (!connected || !client) return
      void listSessions(client)
        .then(setChats)
        .catch((e) =>
          failChatsLoad(String((e as Error)?.message || e) || 'could not read your conversations'),
        )
    },
    [connected, client],
  )

  /* A conversation to type into, and the list of the saved ones. Both wait for the socket: a
     window that lists nothing because it asked too early looks like a window with no history. */
  useEffect(() => {
    if (!connected || !client) return
    if (!currentKey) newSession(false) // a session to type into, NOT a view change
    refreshChats()
  }, [connected, client, currentKey, newSession, refreshChats])

  /* THE ACCOUNT CHANGED — signed in, signed out, or switched. Every row in the rail belongs to
     whoever was signed in a moment ago, so they are DROPPED and re-read rather than left standing
     until a replacement happens to arrive. That gap is the whole complaint: a switch took long
     enough that the previous user's conversations sat there looking like the new user's.

     `onIdentityChanged` and not a daemon frame: it is the SDK's own identity bus, it fires only
     on a genuine change of the resolved account (not on a token refresh), and it is what actually
     exists — the vendored client emits no `auth.changed` event at all. */
  useEffect(() => onIdentityChanged(() => refreshChats(true)), [refreshChats])

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
    /* Captured, because this resolves LATER and `currentKey` will have moved on if the user
       clicks a second row while the first is still in flight. Writing the answer into whatever
       is open now would drop one conversation's transcript into another. */
    const key = currentKey
    /* SAID BEFORE IT IS ASKED FOR. The flag is what the conversation column reads to draw a
       spinner instead of the opening screen, and it has to be set in the same tick as the
       request or there is a frame where the chat is empty, not loading, and showing four cards
       inviting the user to start something else. */
    useApp.getState().patch(key, { loadingHistory: true })
    void loadHistory(client, key)
      .then((items) => {
        // Only fill if still empty — a run may have started streaming while the fetch was in
        // flight, and the live items win. `patch`, not `openSession`: the session already exists
        // (seeded empty), and openSession deliberately never overwrites an existing one's items.
        const now = useApp.getState().sessions[key]
        if (!now) return
        useApp.getState().patch(key, {
          loadingHistory: false,
          ...(items.length && now.items.length === 0 ? { items } : {}),
        })
      })
      .catch(() => {
        // FORGOTTEN, so a second click asks again. Leaving the key in the set would make one
        // failed read permanent for the life of the page: the row would stay clickable and go on
        // opening an opening screen where a conversation is.
        historyTried.current.delete(key)
        useApp.getState().patch(key, { loadingHistory: false })
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
  /* A SAVED CONVERSATION ON ITS WAY IS NOT AN EMPTY ONE. Both have nothing in `items`, which is
     why `empty` alone could never tell them apart — and getting it wrong puts the opening screen
     over the chat the user just clicked. */
  const loadingHistory = session.loadingHistory
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
          client && (
            /* NO BYOK GROUP. Those keys only matter on a daemon running against the
               user's own provider accounts; this agent is web-delivered and metered by
               the platform, with no local mode to switch into — so the fields could never
               take effect, and a settings page offering a dead control makes the live ones
               look doubtful too. (This agent declares no settings at all — the instance is
               provisioned and the model keys are the platform's — so the page is intentionally
               near-empty.) */
            <Settings client={client} agentId={AGENT_ID} hideSecrets />
          )
        ) : (
          /* THE STUDIO: conversation beside a live dashboard of what the run produced
             (design_handoff_agent_studio). The dashboard replaced the old stat aside — its
             KPI row carries the same numbers from the same sources. */
          <div className={`st-cols${chatSide === 'right' ? ' is-chat-right' : ''}`}>
            {/* Width is INLINE because it is user state, not design state — the stylesheet owns
                the minimum, this owns what the person dragged it to. */}
            <div className="st-convo" style={{ width: chatWidth }}>
              <div className="st-convo-head">
                <span className="st-live-dot" />
                <div className="st-convo-titles">
                  <span className="st-convo-title">
                    {/* The rail already knows this conversation's name, so the header can carry it
                        while the transcript is still coming. Falling back to the agent's name here
                        would say "Comfy Artchitect" over a chat that is demonstrably not new. */}
                    {loadingHistory
                      ? openChat?.title || 'Opening conversation…'
                      : empty
                        ? AGENT_NAME
                        : openChat?.title || 'New conversation'}
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
                {loadingHistory ? (
                  /* WAITING, AND SAYING SO. Deliberately not the opening below: the four cards
                     invite the user to start a DIFFERENT chat from the one they just clicked, and
                     a transcript that then lands underneath makes the window look as if it
                     changed its mind. Nothing here is clickable, because there is nothing useful
                     to do for the second or two this lasts. */
                  <div className="chat-loading">
                    <Loader2 className="ld-spin" size={20} strokeWidth={1.8} />
                    <span>Opening conversation…</span>
                  </div>
                ) : empty ? (
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
                  <Thread
                    items={session.items}
                    running={session.running}
                    onSuggest={seedComposer}
                    /* A ticked-boxes verdict SENDS. The user already made the deliberate choice
                       in the checkboxes; asking them to press Enter afterwards asks twice. */
                    onDecide={(reply) => void send(reply)}
                  />
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

            {/* Between the two columns in DOM order, so `is-chat-right` ordering carries it to
                the correct edge without a second element. */}
            <ChatResizer side={chatSide} />

            <StudioDashboard
              client={client ?? undefined}
              running={session.running}
              artifacts={artifacts}
              credits={credits}
              onCredits={() => setView('credits')}
            />
          </div>
        )}
      </main>
    </div>
  )
}
