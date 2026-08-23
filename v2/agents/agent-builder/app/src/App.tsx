/* Agent Builder — the shell, and the wiring between its three regions.
 *
 *   rail       which conversation, and the way into settings
 *   main       the conversation
 *   inspector  the agent being built: its files, and the three things you can do to it
 *
 * FOCUS BELONGS TO A CONVERSATION. What the inspector points at is decided by the conversation —
 * chosen in the hero, created by the chat itself, or read back out of a resumed transcript — and
 * never re-pointed mid-thread, because a panel you can aim somewhere else is a panel that can
 * disagree with the conversation beside it. To work on something else, start a new chat.
 *
 * SETTINGS IS A MODAL, NOT A VIEW. It used to be the second half of a two-way switch in the rail,
 * which made "configure the thing" and "use the thing" mutually exclusive: opening settings closed
 * the conversation you opened them because of. A modal is the honest shape — you go in, change
 * something, and come back to exactly what you left.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAgentFiles } from './agentd/agent-files'
import { buildAndOpen, hasWindow } from './agentd/app-window'
import { MAX_FILES } from './agentd/chat'
import { useCredits } from './agentd/credits'
import SignIn from '../../skills/build-agent/templates/_common/auth/SignIn'
import { AGENT_ID, useClient } from './agentd/client'
import { usePlatform, useRestartDaemon, useWhoAmI } from './agentd/platform'
import { openable } from './agentd/roster'
import { forkSession } from './agentd/sessions'
import { installSoftScroll } from './lib/softScroll'
import { useApp, useSession, useSubject } from './state/store'
import { Composer } from './components/Composer'
import { ContextRing } from './components/ContextRing'
import { Inspector } from './components/Inspector'
import { Sidebar } from './components/Sidebar'
import Credits from '../../skills/build-agent/templates/_common/credits/Credits'
import { MyAgentsView } from './components/MyAgentsView'
import { SettingsView } from './components/settings/SettingsView'
import { StartModal, type StartMode } from './components/StartModal'
import { HeroStart, HeroSuggestions } from './components/Hero'
import { Thread } from './components/Thread'
import TabBar from './components/TabBar'
import { Topbar } from './components/Topbar'

export default function App() {
  const { client, status } = useClient()
  const ready = status === 'open'

  const [daemonVersion, setDaemonVersion] = useState('')

  /* THE SHELL'S STATE LIVES IN THE STORE, so the sidebar can read it the way agentd's does.
   *
   * WHICH SCREEN the stage is showing is still two and only two: the conversation you are having,
   * and the shelf of what you have built. Everything else in this window is a modal, which is the
   * shape that does not close the thing you opened it because of (see SettingsModal). */
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const agents = useApp((s) => s.agents)
  /* THE ACTIVE CONVERSATION, and the agent it is about. Both come from the store now: more than
     one conversation can be open, so a hook holding one of anything cannot answer for them. The
     subject is a field OF the session, which is what makes the inspector follow the tab. */
  const chat = useSession()
  const selected = useSubject()
  const sessionKey = useApp((s) => s.currentSessionKey)
  const newSession = useApp((s) => s.newSession)
  const openSession = useApp((s) => s.openSession)
  const setScope = useApp((s) => s.setScope)
  const setIntent = useApp((s) => s.setIntent)
  const sendMessage = useApp((s) => s.sendMessage)
  const abortRun = useApp((s) => s.abortRun)
  const addFiles = useApp((s) => s.addFiles)
  const removeFile = useApp((s) => s.removeFile)
  const toolTick = useApp((s) => s.toolTick)
  const sidebarCollapsed = useApp((s) => s.sidebarCollapsed)
  const panelOpen = useApp((s) => s.panelOpen)
  const togglePanel = useApp((s) => s.togglePanel)
  const connectStore = useApp((s) => s.connect)
  // Which start dialog is open, and the suggestion that opened it. One piece of state, because
  // "create" and "edit" are two questions asked by one screen and never both at once.
  const [start, setStart] = useState<{ mode: StartMode; seed?: string } | null>(null)
  const [windowError, setWindowError] = useState('')
  const [opening, setOpening] = useState(false)
  const [forking, setForking] = useState(false)
  const [forked, setForked] = useState(false)

  const who = useWhoAmI(client, status)
  // A SECOND instance of this hook lives in SettingsView, deliberately. Each holds its own
  // copy of one immutable-ish fact and costs one status call; hoisting it to share would mean
  // threading platform state through the whole tree to save a request at boot. The settings
  // modal is mounted only while open, so it re-reads after a sign-out from here and the two
  // cannot drift where anyone can see it.
  const platform = usePlatform(client)
  const daemon = useRestartDaemon(client)

  /* Soft scroll edges, app-wide and automatic: every scroll container — this one, the sidebar,
   * the inspector, a settings pane opened later — gets a fade wherever it is actually clipping
   * content, plus a scrollbar that only appears while it is being used. Installed once, at the
   * root, because it finds containers itself rather than being wired per component. */
  useEffect(() => installSoftScroll(), [])

  /* Attach the store to the socket once it is OPEN, and again on every later open: signing in
   * re-dials with a new session, and both lists have to be re-read as the new identity. `connect`
   * tears its old subscriptions down first, so calling it twice cannot stack handlers.
   *
   * The newborn-focus rule moved in there with the roster — an agent that did not exist a moment
   * ago was just BUILT, in this window, by this conversation, so it takes focus, and never steals
   * it from an agent already being worked on. */
  useEffect(() => {
    if (ready) connectStore(client)
  }, [ready, client, connectStore])

  const files = useAgentFiles(client, selected?.id ?? null)
  /* A tool finishing in the OPEN conversation may have written files; re-read the tree. The store
     bumps a counter rather than calling back into its subscribers — a store that calls back is a
     store that has to know who they are. */
  const refreshFiles = useRef(files.refresh)
  refreshFiles.current = files.refresh
  useEffect(() => {
    if (toolTick) void refreshFiles.current()
  }, [toolTick])

  // HOW FULL THIS CONVERSATION IS. Keyed to the OPEN session so switching chats never shows the
  // previous one's number — the daemon reports per session and the hook filters on it.
  const credits = useCredits(client, chat.running)

  // Boot, on the first open — the same order and the same place the vanilla window used.
  const booted = useRef(false)
  useEffect(() => {
    if (!ready || booted.current) return
    booted.current = true
    void (async () => {
      try {
        const hello = await client.hello()
        setDaemonVersion(hello?.version ? `v${hello.version}` : '')
      } catch {
        // advisory only — the version chip is the least important thing on screen
      }
    })()
  }, [ready, client])

  /** Work on an existing agent, in a conversation of its own.
   *
   *  A FRESH CHAT, not a re-point of the open one. Focus belongs to a conversation (see the file
   *  header): re-aiming it mid-thread leaves a transcript whose first half is about one agent and
   *  whose second half is about another, and a resumed copy of it can only pick one. */
  const editAgent = useCallback(
    (id: string) => {
      const agent = agents.find((a) => a.id === id)
      if (!agent) return
      setStart(null)
      // A FRESH CONVERSATION, then point it: the scope IS the inspector's subject, so one call
      // does both jobs that used to need two.
      newSession()
      setScope(agent)
    },
    [agents, newSession, setScope],
  )

  /** Start building something new, having answered the one question worth asking first. */
  const createAgent = useCallback(
    (window: boolean, seed?: string) => {
      setStart(null)
      newSession() // a fresh conversation, about nothing until something is built
      setIntent({ window })
      if (seed) void sendMessage(seed)
    },
    [newSession, setIntent, sendMessage],
  )

  // Does this agent COMPILE its window? `app/` at the top of its tree is the whole test: source
  // there, build output in ui/. A hand-written ui/ has nothing to build and is live on save.
  const compiles = files.rows.some((r) => r.name === 'app' && r.depth === 0 && r.kind === 'folder')

  /** BUILD, THEN OPEN. The button means "show me my current source", not "show me the last
   *  build" — see buildAndOpen. Failure shows vite's error and opens nothing. */
  const openWindow = useCallback(async () => {
    if (!selected) return
    setWindowError('')
    setOpening(true)
    try {
      await buildAndOpen(client, selected, compiles)
      void files.refresh() // the build wrote ui/; let the tree flash what changed
    } catch (e) {
      // Surfaced, never swallowed: a button that silently does nothing is worse than one that
      // says the build failed, or that the pop-up was blocked.
      setWindowError(String((e as Error)?.message || e))
    } finally {
      setOpening(false)
    }
  }, [selected, client, compiles, files])

  /** Resume a saved conversation, and point the inspector at whatever that conversation was
   *  about. The transcript names it (see `subjectOf`); an unscoped chat that built nothing names
   *  nothing, and an empty panel is then the truthful answer rather than a stale one. */
  const openChat = useCallback(
    async (key: string) => {
      const id = await openSession(key)
      if (!id) return
      const agent = agents.find((a) => a.id === id)
      if (agent) setScope(agent)
    },
    [openSession, agents, setScope],
  )

  /* Built once, rendered in one of two places: centred in an empty page, or pinned under a
     conversation. Two copies of this JSX would be two things to keep in step. */
  const empty = chat.items.length === 0
  /* HAS THIS CONVERSATION BEEN STARTED? Not "does it have messages" — a chat you just created or
     picked an agent for has none yet, and it is emphatically not untouched.
     
     This is the bug that made every screen look like the same screen: the empty state WAS the
     Start card, so answering "create a new agent" in the dialog dropped you on a page offering to
     create a new agent. You could not tell it had worked. Only a genuinely fresh chat — no
     messages, no subject, no window decision — asks how to begin. */
  const fresh = empty && !chat.scope && !chat.intent
  const composer = (
    <Composer
      running={chat.running}
      pending={chat.pending}
      onSend={(text) => void sendMessage(text)}
      onAbort={() => void abortRun()}
      onFiles={(list) => void addFiles(list)}
      onRemoveFile={removeFile}
      onOpenWindow={hasWindow(selected) ? () => void openWindow() : undefined}
      openWindowLabel={opening ? 'Building…' : undefined}
      openWindowBusy={opening}
      onFork={chat.items.length ? () => void forkChat() : undefined}
      forkLabel={forking ? 'Forking…' : forked ? 'Forked — you are in the copy' : undefined}
      forkBusy={forking}
      meter={<ContextRing usage={chat.usage} />}
      connected={ready}
      // The model that ACTUALLY ran the last step, which under cost-efficiency routing is not the
      // one configured — the daemon reports it with each usage event.
      model={chat.usage?.model || ''}
      credits={credits}
      onCredits={() => setView('credits')}
      maxFiles={MAX_FILES}
    />
  )

  /** Fork this conversation: a full copy you can take in another direction, leaving this one as
   *  it stands. The copy OPENS HERE, which also restores its agent scope — the subject is read
   *  back out of the transcript, so a fork lands pointed at the same agent.
   *
   *  IT SAYS SO, which it did not. The copy is identical to what you were already looking at, so
   *  landing in it changed nothing on screen: no busy state, no confirmation, nothing to tell you
   *  the click had worked. People clicked again. And again — each one a real fork, all of them
   *  piling up in Recents. `forking` closes the door while the request is in flight and the button
   *  reports what happened afterwards.
   *
   *  GUARDED AT THE TOP as well as by the disabled button: `disabled` is a render away, and two
   *  clicks inside one frame both get through it. */
  const forkChat = useCallback(async () => {
    if (forking) return
    setWindowError('')
    setForking(true)
    try {
      const key = await forkSession(client, sessionKey)
      await openChat(key)
      setForked(true)
      setTimeout(() => setForked(false), 1600)
    } catch (e) {
      setWindowError(String((e as Error)?.message || e))
    } finally {
      setForking(false)
    }
    // `openChat` was missing here, so this closure kept the one built on the FIRST render — and
    // with it that render's `chat`. A fork could open into a stale conversation object.
  }, [forking, client, sessionKey, openChat])

  // NOTHING IN FOCUS -> NO PANEL. Not an empty panel with a placeholder in it: there is no
  // question for the panel to answer yet, and a column of dashes is furniture.
  /* THE INSPECTOR BELONGS TO THE CONVERSATION, so it goes with it. It shows what the open chat is
     ABOUT — and beside a settings page or the agent shelf it is answering a question nobody on
     that screen asked, while taking 360px from one that needs the width. */
  const showPanel = view === 'chat' && !!selected && panelOpen
  const shellClass = ['shell', sidebarCollapsed ? 'rail-icons' : '', showPanel ? '' : 'no-panel']
    .filter(Boolean)
    .join(' ')

  /* THE SIGN-IN CARD, over everything. agentd's own, copied into _common/auth — this window used
     to call the SDK's vanilla gate, which built its own DOM on top of the page. Raised from the
     account menu; it takes the whole window because signing in is not something to do alongside
     something else. */
  if (platform.wantsSignIn) return <SignIn product="Agent Builder" onDone={platform.signedIn} />

  return (
    <div className={shellClass}>
      <Sidebar
        openKey={sessionKey}
        onOpenChat={(key) => {
          setView('chat')
          void openChat(key)
        }}
        // Picking an agent in the sidebar IS editing it — the same call the picker makes. A
        // sidebar row that only highlighted something would be a click that does nothing.
        onPickAgent={(id) => {
          setView('chat')
          editAgent(id)
        }}
        onCreate={() => setStart({ mode: 'create' })}
        onEdit={() => setStart({ mode: 'edit' })}
        onSettings={() => setView('settings')}
        onCredits={() => setView('credits')}
        auth={platform.auth}
        authError={platform.error}
        onSignIn={platform.signIn}
        onSignOut={platform.signOut}
        status={status}
        daemonVersion={daemonVersion}
      />

      {/* A VIEW REPLACES THE WHOLE MAIN AREA, the way agentd's do — its TabBar lives inside its
          ChatView, not above the router. Both used to render here, so opening Settings left the
          chat's tab strip and its "What should we build?" header sitting on top of a settings
          page: two screens at once, and a strip of conversations above a thing that is not one.
          Each branch below now brings whatever header it needs. */}
      <main className="main">
        {view === 'credits' ? (
          /* THE SAME PAGE THE ASSISTANT SHOWS, copied into _common/ and rendered here. Running out
             of credits is the one failure a user can fix themselves, so it is a place you can go
             rather than a dialog over the thing that just stopped. */
          <Credits agentId={AGENT_ID} />
        ) : view === 'settings' ? (
          /* A PAGE, not a modal. It was a modal so that configuring the thing did not close the
             conversation you opened it because of — but it is tabbed now, and a tab strip inside
             a floating card is a page pretending not to be one. The conversation is one click
             away in the rail and its tab is still open. */
          <SettingsView client={client} />
        ) : view === 'myagents' ? (
          <MyAgentsView
            agents={openable(agents)}
            onEdit={(id) => {
              setView('chat')
              editAgent(id)
            }}
          />
        ) : (
          <>
            {/* The strip CHOOSES the conversation and the header DESCRIBES the one chosen — so the
                strip goes above. Both belong to the chat and to nothing else. */}
            <TabBar />
            <Topbar
              agent={selected}
              who={who}
              onRestart={() => void daemon.restart()}
              restarting={daemon.busy}
              restartNote={daemon.note}
              canTogglePanel={!!selected}
              panelOpen={panelOpen}
              onTogglePanel={togglePanel}
            />
        <section className="stage">
          {empty ? (
            /* NOTHING SAID YET: the input in the MIDDLE of the page rather than pinned to the
               bottom, which on an empty chat left the one thing you came to use furthest from
               where you were looking. The cards around it appear only on a chat that has not been
               started — see `fresh`. */
            <div className="chat-hero">
              {fresh && (
                <HeroStart
                  hasAgents={openable(agents).length > 0}
                  onCreate={() => setStart({ mode: 'create' })}
                  onEdit={() => setStart({ mode: 'edit' })}
                />
              )}
              <div className="chat-hero-composer">{composer}</div>
              {/* A suggestion says WHAT to build; the dialog is still HOW. Routing it through the
                  same door is what stops the most-taken path being the one that skips the
                  question. */}
              {fresh && (
                <HeroSuggestions onSuggest={(text) => setStart({ mode: 'create', seed: text })} />
              )}
            </div>
          ) : (
            <>
              <Thread items={chat.items} running={chat.running} />
              {composer}
            </>
          )}
          {windowError && (
            <div className="composer-error" role="alert">
              {windowError}
              <button className="icon-btn" onClick={() => setWindowError('')} title="Dismiss">
                ✕
              </button>
            </div>
          )}
        </section>
          </>
        )}
      </main>

      {showPanel && (
        <Inspector
          agent={selected}
          client={client}
          files={files}
          onChanged={() => void files.refresh()}
        />
      )}

      {/* Mounted only while open, which is what loads it: the vanilla window called
          Settings.load() on every switch to that view, and mounting does the same thing without a
          second place to remember it. */}
      {start && (
        <StartModal
          mode={start.mode}
          agents={openable(agents)}
          seed={start.seed}
          onCreate={createAgent}
          onEdit={editAgent}
          onClose={() => setStart(null)}
        />
      )}

    </div>
  )
}
