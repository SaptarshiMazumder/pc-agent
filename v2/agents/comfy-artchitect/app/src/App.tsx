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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { onIdentityChanged } from '@agentd/client'
import {
  Loader2,
  Menu,
  PanelLeft,
  PanelRight,
  Workflow as WorkflowIcon,
  Info,
  Mail,
} from 'lucide-react'

import { AGENT_ID, useClient } from './agentd/client'
import { useCredits } from './agentd/credits'
import { handleRunEvent, jobsFromStatus } from './agentd/run-events'
import { MAX_FILES } from './agentd/chat'
import { useRun } from './agentd/run'
import {
  deleteSession,
  forkSession,
  listSessions,
  loadHistory,
  renameSession,
} from './agentd/sessions'
import { useApp, useSession } from './state/store'

import { BackgroundJobsStrip } from './components/BackgroundJobsStrip'
import { ContextRing } from './components/ContextRing'
import { Composer } from './components/Composer'
import { ChatResizer } from './components/studio/ChatResizer'
import { Sidebar } from './components/Sidebar'
import { StarterPrompts } from './components/StarterPrompts'
import { Thread } from './components/Thread'

/* THIS AGENT'S OWN SCREEN, in place of the scaffold's sample widgets. It reads the artifacts the
   runs really declared, so an empty shelf is a fact about the agent rather than a sign that
   nobody finished the window. */
import MyCreations from './components/creations/MyCreations'
import PolicyPage from './components/policies/PolicyPage'
import { BrandMark } from './components/BrandMark'
import { collectWorkflows } from './components/workflows/WorkflowCard'
import { useGpuWarmup } from './components/studio/useGpuWarmup'
import { useHumanActivity } from './components/studio/useHumanActivity'
import { StudioDashboard } from './components/studio/StudioDashboard'
import type { Artifact } from './agentd/artifacts'
import { referencesReadyInstruction, useReferenceSlots } from './agentd/reference-slots'
import { mergeFiles, useChatWorkspaceFiles } from './agentd/workspace-files'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'

/* WHAT THIS AGENT IS, in the user's words rather than yours — the opening screen's whole job.
   Edit these four lines and the four cards below; they are the first thing anyone reads, and the
   default text says nothing because only you know what this agent is for. */
const AGENT_NAME = 'Comfy Penguin'
const OPENING_HEADLINE = 'What should we build?'
/* NO EYEBROW ABOVE THE HEADLINE. It read "Point me at your ComfyUI", which asked the visitor for
   a setup step before it had told them what they were setting up.

   THE BLURB SAYS WHOSE HARDWARE RUNS THE GRAPH, because that is the first thing a visitor wants
   to know and the policy pages say the same: the platform rents the GPU, the visitor brings
   nothing. It used to say "your instance" and "your box", which was the opposite of true. */
const OPENING_BLURB =
  'Tell me what to make. I rent a GPU for you, set ComfyUI up on it, design the graph, run it, ' +
  'and repair whatever the server rejects until the result is right. You get the images, the ' +
  'workflow file, and an installer to run it on a ComfyUI of your own.'


export default function App() {
  const { client, status } = useClient()
  const connected = status === 'open'

  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const newSession = useApp((s) => s.newSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  const chats = useApp((s) => s.chats)

  /* ── THE TWO DRAWERS (narrow viewports only) ──────────────────────────────────────────────
     On a phone the studio's two panes cannot sit beside the chat: the conversation floors at
     430px and the dashboard at 560px, so the row needs 996px and a 390px screen used to get a
     sideways-scrolling canvas. The rail had it worse -- it was simply `display: none` below
     820px, with no toggle anywhere, so navigation, credits and settings were unreachable.

     THE CHAT IS THE PAGE; the other two slide over it. One state, not two booleans, because
     "only one open at a time" is then structural rather than a rule to remember -- two open
     drawers would fight over the scrim, the focus and the Escape key.

     WIDE VIEWPORTS NEVER READ THIS. The stylesheet ignores the class above 820px, so the
     desktop layout is untouched and the resizer still owns the chat's width there. */
  const [drawer, setDrawer] = useState<'none' | 'rail' | 'workspace'>('none')
  /* Which branch `main` renders. It was computed inline on the className and needed a second
     reader the moment the drawer handles moved out of the studio's own header -- see the bar
     below. `is-studio` and the bar MUST agree: a handle offering a workspace that the current
     view does not render is the same bug as a view with no handle at all. */
  /* NO 'settings'. The page is gone (see the rail), so the view it named renders nothing --
     and leaving it here would have made `main` fall through to an empty screen rather than to
     the studio if anything ever set it. */
  const isStudio = !['credits', 'orgs', 'creations', 'about', 'contact'].includes(view)
  const drawerOpener = useRef<HTMLButtonElement | null>(null)
  const drawerRef = useRef<HTMLDivElement | null>(null)

  /* CLOSES ON ARRIVAL, keyed to the destination rather than to the click. A rail item, a
     conversation in the list and My creations' "open this chat" all navigate through different
     callbacks -- and one of them goes straight to the store, where no wrapper of ours would see
     it. Watching what changed catches every route in, including the ones added later. */
  useEffect(() => { setDrawer('none') }, [view, currentKey])

  useEffect(() => {
    if (drawer === 'none') return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawer('none') }
    window.addEventListener('keydown', onKey)
    // Focus follows the panel, or a keyboard user tabs through the chat behind it.
    drawerRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [drawer])

  const openDrawer = useCallback(
    (which: 'rail' | 'workspace', e: React.MouseEvent<HTMLButtonElement>) => {
      drawerOpener.current = e.currentTarget
      setDrawer((now) => (now === which ? 'none' : which))
    },
    [],
  )

  // Back to the button that opened it -- dumping focus at the top of the document is how a
  // keyboard user loses their place entirely.
  const closeDrawer = useCallback(() => {
    setDrawer('none')
    drawerOpener.current?.focus()
  }, [])
  const seedComposer = useApp((s) => s.seedComposer)
  const session = useSession()
  const sessions = useApp((s) => s.sessions)

  const { send, abort, addFiles, removeFile, addReference, flushReferences, requestDeletion } =
    useRun(client)

  // ONE POLLER FOR THE GPU, here rather than in the top bar's chip, because two things read
  // it now: the chip, and the resume below. Two hooks would be two pollers asking the platform
  // the same question.
  //
  // "ACTIVE" IS WHAT KEEPS THE MACHINE ALIVE: a human in this window (visible tab, recent
  // input) or a run going on any of this window's chats. Either one, and the hook touches the
  // platform once a minute. Neither, and ten minutes later the machine is reaped — the rule as
  // set, measured by people and runs rather than by which tool the agent happens to be using.
  const humanHere = useHumanActivity()
  const anyRunning = useApp((s) => Object.values(s.sessions).some((x) => x.running))
  const gpu = useGpuWarmup(client, true, humanHere || anyRunning)

  /* THE "CONTINUE" BUTTON, PRESSED BY CODE. A turn that ends while the machine is still
     coming up leaves the agent asleep until something wakes it, and that something used to be
     the user — "so will u automatically do it? are u monitoring it urself?" — clicking Continue
     every few minutes. The window IS monitoring it; this is the click. Only for a turn that
     ended cleanly while waiting (awaitingGpu), only while nothing is running, and only once:
     the send clears the flag, so a turn that ends waiting again earns its own resume. */
  useEffect(() => {
    if (gpu.state !== 'ready' || !currentKey) return
    const cur = useApp.getState().sessions[currentKey]
    if (!cur || cur.running || cur.loadingHistory || !cur.awaitingGpu) return
    useApp.getState().patch(currentKey, { awaitingGpu: false })
    void send('The GPU is ready now — continue from where you stopped.')
  }, [gpu.state, gpu.url, currentKey, send])

  /* THE SECOND HALF OF "ADD REFERENCE MEDIA", sent when it becomes legal to send it.
     The file itself went up the moment it was picked — an upload never had to wait for anything.
     What had to wait is the sentence naming it, because a turn cannot be sent while one is
     running; so the paths sat in `pendingReferences` and this is what hands them over. Same
     shape as the resume above: watch for the condition, clear the flag, send once. Before this,
     both halves were gated together and the button was simply dead for the whole turn — which is
     precisely when the agent is mid-install and about to ask for the very photo you are holding. */
  useEffect(() => {
    if (!currentKey) return
    const cur = sessions[currentKey]
    if (!cur || cur.running || cur.loadingHistory || !cur.pendingReferences.length) return
    void flushReferences()
  }, [sessions, currentKey, flushReferences])

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
  /* WHAT IS ON DISK IN THIS CHAT'S FOLDERS, beside what the thread declared. The folders are the
     truth (agentd/workspace-files.ts): a file the agent wrote is listed because it exists, not
     because every hop between the tool and this window agreed to mention it. One merged list,
     made once here, feeds the file rail, the workflow shelf and the header alike. */
  const workspaceVersion = useApp((s) => s.workspaceVersion)
  const listed = useChatWorkspaceFiles(client ?? undefined, currentKey, workspaceVersion)
  const files = useMemo(() => mergeFiles(artifacts, listed, currentKey), [artifacts, listed, currentKey])

  /* REFERENCE SLOTS — the roles the agent asked for, matched to the files in this chat's folder
     (agentd/reference-slots.ts). The rail shows them; the run tool on the daemon reads the same
     folder, so what the rail says is filled IS what will run. */
  const { slots, free } = useReferenceSlots(session.items, files, currentKey)
  /* THE ONE MESSAGE WHEN THE LAST SLOT FILLS — never one per file, and never on a reload of a
     chat whose slots were already full: `armed` is set by a fill made in THIS window. Sent AT
     ONCE, run or no run: a live turn takes it as an interjection (the daemon queues it for the
     model's next step, right after the tool it is in), an idle chat starts a turn with it. It
     used to wait for the run to end and then send — and a Stop ended the run, so pressing Stop
     started a new run in the user's name. */
  const armedRef = useRef(false)
  useEffect(() => {
    if (!armedRef.current || !currentKey || !slots.length || slots.some((s) => !s.file)) return
    const cur = sessions[currentKey]
    if (!cur || cur.loadingHistory) return
    armedRef.current = false
    void send(referencesReadyInstruction(slots), { origin: 'reference' })
  }, [slots, currentKey, sessions, send])
  const onAddReference = useCallback(
    async (file: File, role: string | null): Promise<void> => {
      // The previous holder of the role, whatever its extension, goes: one file per slot.
      const replacing = role ? slots.filter((s) => s.role === role && s.file).map((s) => s.file!.name) : []
      await addReference(file, role, replacing)
      if (role) armedRef.current = true
    },
    [addReference, slots],
  )
  /* The newest emitted workflow's API file — the conversation header's subtitle, so the run
     the studio is about is named right over the transcript. */
  const latestWorkflow = useMemo(() => {
    const wf = collectWorkflows(files)[0]
    return wf?.api?.name || wf?.ui?.name || ''
  }, [files])

  const chatSide = useApp((s) => s.chatSide)
  const chatWidth = useApp((s) => s.chatWidth)
  const setChatSide = useApp((s) => s.setChatSide)

  /* THE BALANCE, beside the thing that spends it. Re-read when a run ends, because that is when
     it changed. `null` means "not known" — a build with no accounts service, where showing a
     zero would be a lie. */
  const credits = useCredits(client!, session.running, gpu.state === 'ready')

  /* ONE auth state for the window. It lives here rather than in the Sidebar because the sign-in
     card is rendered here too, and two `useAuth()` calls would be two states that disagree about
     whether the card is open. */
  const account = useAuth(client!)

  /* ONE SUBSCRIPTION, TORN DOWN ON RECONNECT. Signing in re-dials the socket, and without the
     cleanup each dial would stack another handler — every frame then folded twice, so a streamed
     answer arrived with every character doubled. */
  useEffect(() => {
    if (!client) return
    const off = client.on('chat.event', (payload: any) => handleRunEvent(payload, client))
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
      if (!sessions[key].running && !sessions[key].jobs.length) continue
      void (async () => {
        let running = false
        try {
          const st = (await client.request('chat.status', { sessionKey: key })) as {
            running?: boolean
            jobs?: unknown[]
          }
          running = !!st?.running
          // The jobs as the daemon has them now: ours may be stale (one ended while the socket
          // was down) — and a job's result, if it landed meanwhile, is in the transcript.
          useApp.getState().patch(key, { jobs: jobsFromStatus(st?.jobs) })
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

  /* THE SOCKET DROPPED MID-RUN. Nothing on screen changes by itself — the spinners belong to a
     daemon that can no longer send — so say so on every running chat the moment the status
     leaves 'open': each tool still "running" is stamped interrupted, and the reconnect effect
     above settles whether the run survived. The first time the hosted daemon was OOM-killed
     mid-run, four spinners sat on screen for three minutes without a word. */
  const wasOpen = useRef(false)
  useEffect(() => {
    if (status === 'open') {
      wasOpen.current = true
      return
    }
    if (!wasOpen.current) return
    wasOpen.current = false
    const { sessions, interrupt } = useApp.getState()
    for (const key of Object.keys(sessions)) {
      if (sessions[key].running) {
        interrupt(
          key,
          'Connection to the daemon dropped — the tools shown as running were interrupted. Reconnecting…',
        )
      }
    }
  }, [status])

  /* READING THE SAVED-CONVERSATION LIST — the one place that does it, so the rail's waiting and
     error states have a single owner. A rejection is reported rather than dropped: `listSessions`
     used to be a bare `.then`, which meant a failed read left an empty rail that looked exactly
     like an account with no history. */
  const refreshChats = useCallback(
    (forget = false): Promise<void> => {
      const { beginChatsLoad, setChats, failChatsLoad } = useApp.getState()
      beginChatsLoad(forget)
      /* NOT OVER A SOCKET THAT IS MID-REDIAL. Signing in re-dials the connection, so an identity
         change reaches us while it is between credentials — a read issued now either fails or
         answers for the account that has just left. Marking the list as loading and letting the
         effect below issue it when the socket is back is the same wait with the right answer at
         the end of it, instead of an error flashing up and correcting itself. */
      if (!connected || !client) return Promise.resolve()
      return listSessions(client)
        .then((rows) => {
          setChats(rows)
          /* RUNNING CHATS ARE RE-ATTACHED HERE — the case a browser reload used to lose. A
             reloaded window remembers nothing, so it could not ask `chat.status` for the run it
             was watching; the daemon kept that run going for three minutes and then reaped it,
             and the stream never came back. The rail's `running` flag is the memory: for each
             such chat, ask `chat.status` (which is also the re-attach that cancels the reaper),
             give the session a place in the store so live events land, load what was saved
             while we were away, and mark it running so the rest of the window behaves. */
          for (const row of rows) {
            // A chat with jobs waiting is re-attached like a running one: the strip has to show
            // them, and the turn a finished job starts has to land in a session that exists.
            if (!row.running && !row.jobs) continue
            const key = row.sessionId
            void (async () => {
              let running = false
              let jobs: ReturnType<typeof jobsFromStatus> = []
              try {
                const st = (await client.request('chat.status', { sessionKey: key })) as {
                  running?: boolean
                  jobs?: unknown[]
                }
                running = !!st?.running
                jobs = jobsFromStatus(st?.jobs)
              } catch {
                return
              }
              if (!running && !jobs.length) return
              const st = useApp.getState()
              st.ensureSession(key)
              st.patch(key, { running, jobs, loadingHistory: true })
              try {
                const items = await loadHistory(client, key)
                const now = useApp.getState().sessions[key]
                if (!now) return
                useApp.getState().patch(key, {
                  loadingHistory: false,
                  // Only fill if still empty: live events may have landed while we fetched, and
                  // those win — the saved prefix shows on the next open instead.
                  ...(items.length && now.items.length === 0 ? { items } : {}),
                })
              } catch {
                useApp.getState().patch(key, { loadingHistory: false })
              }
            })()
          }
        })
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

  /* THE LIST IS LIVE NOW, not a snapshot from boot. The daemon broadcasts `sessions.changed` on
     every rename, delete, fork and move — two comments in agentd/sessions.ts have always claimed
     this window subscribed to it, and it never did. So a conversation deleted in another tab, or
     a title the daemon rewrote from the transcript, sat stale here until something else happened
     to refetch. Quiet by design: `refreshChats()` without `forget` keeps the rows on screen while
     it re-reads, so a broadcast never blanks a list somebody is reading. */
  useEffect(() => {
    if (!client) return
    return client.on('sessions.changed', () => void refreshChats())
  }, [client, refreshChats])

  /* DELETING A SAVED CONVERSATION. The daemon refuses one with a live run and says so; that
     message goes to the rail rather than being swallowed, because a button that silently does
     nothing is the worst kind of destructive control.

     The row is dropped LOCALLY first and the list re-read after: the broadcast above will bring
     the same answer a moment later, but the person who pressed the button should not watch their
     own click take a network round trip to land. `closeSession` also moves `currentSessionKey`
     off the deleted chat, and the boot effect makes a fresh one when nothing is left. */
  /* RENAME AND DUPLICATE, the other two items on a conversation's menu. Both are the daemon's
     work; App does the I/O for the same reason it owns the delete — the rail asks, this answers,
     and there is one place that knows how to put the list back in step afterwards.

     NO LOCAL PRE-UPDATE on either, unlike the delete below. A delete removes the row you are
     looking at and waiting a round trip to see it go feels broken; a rename lands on a row that
     stays put, and `sessions.changed` brings the new title back within a frame or two. Guessing
     at it here would only mean two writes and a flicker when they disagree. */
  const renameChat = useCallback(
    async (sessionId: string, title: string): Promise<void> => {
      if (!client) return
      try {
        await renameSession(client, sessionId, title)
        void refreshChats()
      } catch (e) {
        useApp
          .getState()
          .failChatsLoad(String((e as Error)?.message || e) || 'could not rename that conversation')
      }
    },
    [client, refreshChats],
  )

  const duplicateChat = useCallback(
    async (sessionId: string): Promise<void> => {
      if (!client) return
      try {
        // The copy is OPENED, not merely made. A fork you have to go and find in the list is a
        // command whose result is invisible; this is the one case where the new row is the point.
        const key = await forkSession(client, sessionId)
        await refreshChats()
        useApp.getState().openSession(key)
      } catch (e) {
        useApp
          .getState()
          .failChatsLoad(String((e as Error)?.message || e) || 'could not copy that conversation')
      }
    },
    [client, refreshChats],
  )

  const deleteChat = useCallback(
    async (sessionId: string): Promise<void> => {
      if (!client) return
      try {
        await deleteSession(client, sessionId)
        const st = useApp.getState()
        st.setChats(st.chats.filter((c) => c.sessionId !== sessionId))
        st.closeSession(sessionId)
        void refreshChats()
      } catch (e) {
        useApp
          .getState()
          .failChatsLoad(String((e as Error)?.message || e) || 'could not delete that conversation')
      }
    },
    [client, refreshChats],
  )

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
  if (account.wantsSignIn)
    return <SignIn product={AGENT_NAME} mark={<BrandMark size={38} />} onDone={account.signedIn} />

  /* WHAT THIS SCREEN IS ABOUT, from what is actually on it. A title invented from sample text
     would be a lie the first time somebody opened a real conversation. */
  const openChat = chats.find((c) => c.sessionId === currentKey)
  const empty = session.items.length === 0
  /* A SAVED CONVERSATION ON ITS WAY IS NOT AN EMPTY ONE. Both have nothing in `items`, which is
     why `empty` alone could never tell them apart — and getting it wrong puts the opening screen
     over the chat the user just clicked. */
  const loadingHistory = session.loadingHistory
  /* NOTHING SAID YET — so the conversation is the whole screen and the workspace is not drawn
     at all. NOT hidden: `StudioDashboard` polls `.studio/state.json` on a timer and the file
     explorer lists the chat's files, and doing either for a chat that has produced nothing is
     work with no reader. The first send makes `items` non-empty, this flips, the dashboard
     mounts and the column goes back to `chatWidth` — which is still whatever the user last
     dragged it to, because nothing here ever wrote to it. */
  const solo = empty && !loadingHistory
  /* `pct` ARRIVES AS A FRACTION (0-1), not a percentage — the daemon sends `used / limit`
     rounded to 4 places. Rounding it straight to an integer floored every real conversation to
     "0% ctx" (anything under half a window), which read as a broken meter rather than a wrong
     unit. Scale here; the wire format is what agent-builder's ring already consumes. */
  const pct =
    session.usage && session.usage.limit > 0 ? Math.round(session.usage.pct * 100) : null

  return (
    <div className={`shell${drawer === 'none' ? '' : ` drawer-open drawer-${drawer}`}`}>
      {/* The scrim is the affordance AND the dismissal, but never the only one: Escape closes
          too, and the toggle that opened the panel closes it again. Rendered only while a
          drawer is open so it cannot swallow a tap the rest of the time. */}
      {drawer !== 'none' && (
        <div className="drawer-scrim" onClick={closeDrawer} aria-hidden="true" />
      )}

      {/* THE PHONE'S ONLY PERSISTENT CHROME (hidden above 820px). The handles used to live in
          `.st-convo-head`, which exists ONLY in the studio branch -- so Credits, Settings,
          Organizations, My creations, About and Contact each rendered with no rail and no way
          to open one. Reaching Credits on a phone was a one-way trip, which is the very bug
          the drawers were meant to end.

          A SIBLING OF <main>, not a child, so it cannot depend on which view is mounted. The
          shared screens come from src/common/ and this agent must not edit them; the only place
          that reliably survives every branch is out here. */}
      <div className="mobile-bar">
        <button
          className="st-drawer-btn"
          aria-label="Open navigation"
          aria-expanded={drawer === 'rail'}
          onClick={(e) => openDrawer('rail', e)}
        >
          <Menu size={18} strokeWidth={1.8} />
        </button>
        {/* THE MARK, NOT THE NAME. The bar carried the words "Comfy Penguin" and they were a
            second title: the conversation header beneath it already names the chat, and
            `.page-head` already names Credits, My creations, About and Contact. The penguin
            repeats nothing -- it says which app this is without competing with whatever the
            page below has called itself.

            CENTRED ABSOLUTELY rather than by a flex spacer, so it stays on the middle of the
            bar whether or not the workspace handle is rendered on the right. */}
        <span className="mobile-bar-mark" aria-hidden="true">
          <BrandMark size={22} />
        </span>
        {/* Only where there IS one to open: the workspace belongs to the studio, and `solo`
            means the run has produced nothing for it to hold yet. */}
        {isStudio && !solo && (
          <button
            className="st-drawer-btn mobile-bar-end"
            aria-label="Open workspace"
            aria-expanded={drawer === 'workspace'}
            onClick={(e) => openDrawer('workspace', e)}
          >
            <PanelRight size={18} strokeWidth={1.8} />
          </button>
        )}
      </div>
      {/* RELOADS THIS WINDOW when the agent is rebuilt, so building it stops meaning "reopen it
          by hand after every change". Renders nothing, and is inert once the agent is published —
          only the authoring plugin can emit the event it listens for. */}
      <LiveReload client={client ?? undefined} />
      <div
        className="drawer-host drawer-host--rail"
        ref={drawer === 'rail' ? drawerRef : null}
        tabIndex={-1}
        role={drawer === 'rail' ? 'dialog' : undefined}
        aria-modal={drawer === 'rail' ? true : undefined}
        aria-label={drawer === 'rail' ? 'Navigation' : undefined}
      >
      <Sidebar
        view={view}
        onView={setView}
        onNewChat={() => newSession()}
        account={account}
        status={status}
        name={AGENT_NAME}
        counts={{ credits: credits === null ? undefined : credits.toLocaleString() }}
        /* A SCREEN OF THIS AGENT'S OWN, above the shared three. What this agent makes is FILES,
           and files are the one thing a conversation is a bad container for. */
        extraDestinations={[
          { id: 'creations', label: 'My creations', icon: <WorkflowIcon size={15} /> },
        ]}
        /* ABOUT AND CONTACT ARE SCREENS OF THIS APP, read here like Settings is. The words come
           from the shipped HTML files (components/policies), which stay readable with no
           account and no JavaScript for anyone who arrives from outside; Terms, Privacy,
           Refunds and Delivery are one click away inside either page. Two rows, not six. */
        afterDestinations={[
          { id: 'about', label: 'About us', icon: <Info size={15} /> },
          { id: 'contact', label: 'Contact us', icon: <Mail size={15} /> },
        ]}
        /* The rail asks; App does the I/O. Both return promises so the buttons can show their
           own progress for exactly as long as the work takes. */
        onRefreshChats={() => refreshChats()}
        onDeleteChat={deleteChat}
        onRenameChat={renameChat}
        onDuplicateChat={duplicateChat}
      />
      </div>

      {/* `is-studio` must track the SAME condition as the branch below — an unknown view falls
          through to the studio, and a modifier keyed to 'chat' alone would lay it out wrong. */}
      <main className={`main${isStudio ? ' is-studio' : ''}`}>
        {view === 'credits' ? (
          <Credits agentId={AGENT_ID} />
        ) : view === 'orgs' ? (
          <OrgView client={client ?? undefined} />
        ) : view === 'creations' ? (
          /* EVERY CHAT'S FILES, not this one's: the library reads the workspace folders itself
             (agentd/chat-library.ts) and needs the chat list only for the section titles.
             Opening a section switches to that conversation — the studio branch below then
             loads its transcript, the same as a click in the rail. */
          <MyCreations
            client={client ?? undefined}
            chats={chats}
            workspaceVersion={workspaceVersion}
            onOpenChat={(key) => {
              useApp.getState().openSession(key)
              setView('chat')
            }}
          />
        ) : view === 'about' ? (
          <PolicyPage key="about" start="about.html" />
        ) : view === 'contact' ? (
          <PolicyPage key="contact" start="contact.html" />
        ) : (
          /* THE STUDIO: conversation beside a live dashboard of what the run produced
             (design_handoff_agent_studio). The dashboard replaced the old stat aside — its
             KPI row carries the same numbers from the same sources. */
          <div
            className={`st-cols${chatSide === 'right' ? ' is-chat-right' : ''}${solo ? ' is-solo' : ''}`}
          >
            {/* Width is INLINE because it is user state, not design state — the stylesheet owns
                the minimum, this owns what the person dragged it to. */}
            <div className="st-convo" style={solo ? undefined : { width: chatWidth }}>
              <div className="st-convo-head">
                <span className="st-live-dot" />
                <div className="st-convo-titles">
                  <span className="st-convo-title">
                    {/* The rail already knows this conversation's name, so the header can carry it
                        while the transcript is still coming. Falling back to the agent's name here
                        would say "Comfy Penguin" over a chat that is demonstrably not new. */}
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
                    <h2 className="opening-headline">{OPENING_HEADLINE}</h2>
                    <p className="opening-blurb">{OPENING_BLURB}</p>
                    {/* The four ways in used to be a grid of cards HERE. They moved under the
                        composer (StarterPrompts) — beside the box you would type in anyway,
                        rather than floating in the middle of the screen away from it. */}
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
                <BackgroundJobsStrip jobs={session.jobs} sessionKey={currentKey} client={client} />
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
                  meter={
                    pct === null ? null : (
                      <ContextRing
                        pct={pct}
                        used={session.usage!.used}
                        limit={session.usage!.limit}
                      />
                    )
                  }
                />
                {/* UNDER THE BOX, and only while there is nothing to read. Once a conversation
                    exists these are noise competing with the agent's own `suggest` chips. */}
                {empty && !loadingHistory && <StarterPrompts onPick={seedComposer} />}
              </div>
            </div>

            {/* THE WORKSPACE, and the handle that sizes it — both absent on an empty chat.
                Between the two columns in DOM order, so `is-chat-right` ordering carries the
                handle to the correct edge without a second element. */}
            {!solo && (
              <>
                <ChatResizer side={chatSide} />

                <div
                  className="drawer-host drawer-host--workspace"
                  ref={drawer === 'workspace' ? drawerRef : null}
                  tabIndex={-1}
                  role={drawer === 'workspace' ? 'dialog' : undefined}
                  aria-modal={drawer === 'workspace' ? true : undefined}
                  aria-label={drawer === 'workspace' ? 'Workspace' : undefined}
                >
                <StudioDashboard
                  client={client ?? undefined}
                  gpu={gpu}
                  running={session.running}
                  artifacts={files}
                  slots={slots}
                  freeReferences={free}
                  onAddReference={onAddReference}
                  referencesDisabled={!connected}
                  onRequestDeletion={(paths) => void requestDeletion(paths)}
                  /* Not mid-run: a delete request landing between an emit and its run is the
                     one case worth refusing outright, so it waits rather than queues. */
                  deletionDisabled={
                    !connected
                      ? 'Not connected to the daemon'
                      : session.running
                        ? 'Wait for the current turn to finish'
                        : ''
                  }
                  credits={credits}
                  onCredits={() => setView('credits')}
                />
                </div>
              </>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
