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
import LiveReload from '../../skills/build-agent/templates/_common/dev/LiveReload'
// AGENTD'S OWN OrgView, byte-identical (components/OrgView.tsx), NOT the shared module —
// the requirement is carbon-copy behavior with the assistant, checkable by diffing the two
// files. Agents still get `common/orgs`; this window matches its parent instead.
import OrgView from './components/OrgView'
import { Blueprint } from './components/Blueprint'
import { Launchpad } from './components/Launchpad'
import { TemplateViewer } from './components/TemplateViewer'
import { WorkspaceCapabilities } from './components/WorkspaceCapabilities'
import { WorkspaceFiles } from './components/WorkspaceFiles'
import { WorkspacePreview } from './components/WorkspacePreview'
import { WorkspaceTestDrive } from './components/WorkspaceTestDrive'
import { ShipScreen } from './components/ShipScreen'
import { MyAgentsView } from './components/MyAgentsView'
import { SettingsView } from './components/settings/SettingsView'
import { StartModal, type StartMode } from './components/StartModal'
import { Thread } from './components/Thread'
import TabBar from './components/TabBar'
import { Topbar } from './components/Topbar'

import { slugOf } from './lib/agentSlug'

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
  /** The agent being created right now, so the shell can say so rather than looking frozen. */
  const [creating, setCreating] = useState('')
  const [createError, setCreateError] = useState('')
  /** An agent created but not yet on the roster — see the effect below. Carries the directory
   *  create_agent reported, because nothing else knows it. */
  const [pendingScope, setPendingScope] = useState<{ id: string; dir: string } | null>(null)
  const sendMessage = useApp((s) => s.sendMessage)
  const setWsTab = useApp((s) => s.setWsTab)
  const abortRun = useApp((s) => s.abortRun)
  const addFiles = useApp((s) => s.addFiles)
  const removeFile = useApp((s) => s.removeFile)
  const toolTick = useApp((s) => s.toolTick)
  const openTabs = useApp((s) => s.openTabs)
  const sidebarCollapsed = useApp((s) => s.sidebarCollapsed)
  const panelOpen = useApp((s) => s.panelOpen)
  const togglePanel = useApp((s) => s.togglePanel)
  const connectStore = useApp((s) => s.connect)
  // Which start dialog is open, and the suggestion that opened it. One piece of state, because
  // "create" and "edit" are two questions asked by one screen and never both at once.
  // CREATE now opens the Blueprint page below; this dialog still owns EDIT (the agent picker).
  const [start, setStart] = useState<{ mode: StartMode; seed?: string } | null>(null)
  /** The Blueprint page — the create wizard as a full-bleed overlay. Holds the seed the same
   *  way `start` held it; rendered over the shell (not instead of it) so LiveReload and every
   *  subscription stay mounted underneath, exactly as they did under the modal. `shape` is a
   *  pre-picked Shape tile — the template viewer's "Use this template", or the shelf's
   *  Headless card, neither of which should re-ask what was just chosen. */
  const [blueprint, setBlueprint] = useState<{ seed?: string; shape?: string } | null>(null)
  /** The template viewer — full-screen looking, over the launchpad. Holds the template id. */
  const [viewer, setViewer] = useState<string | null>(null)
  /** The Ship screen — preflight and the two real verbs, over the shell like the Blueprint. */
  const [ship, setShip] = useState(false)
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

  /** Start building something new — by BUILDING IT, then talking about it.
   *
   *  WHAT THIS REPLACED. It used to open a blank conversation and set an `intent` that rode every
   *  message ("we are creating a NEW agent, and it HAS ITS OWN APP WINDOW") until the model got
   *  around to calling create_agent. Two things were wrong with that. The agent did not exist, so
   *  there was nothing to look at and nothing to validate; and whether it ended up with a window
   *  depended on the model reading an instruction, which is a hope rather than a mechanism.
   *
   *  Now the agent is real before the first message. A windowed one already has a complete,
   *  working window — sign-in, credits, settings, organizations — so the conversation starts by
   *  CHANGING something instead of by assembling it.
   */
  const createAgent = useCallback(
    async (name: string, window: boolean, template: string, seed?: string) => {
      if (!client) return
      setStart(null)
      setBlueprint(null)
      setCreating(name)
      setCreateError('')
      try {
        // The id is derived here rather than asked for: two boxes to fill in for one decision,
        // and the second one has exactly one sensible answer.
        const id = slugOf(name)
        const made = await client.invokeTool('create_agent', {
          id,
          name,
          window,
          template,
          // A PLACEHOLDER, and it says so. `identity` is required — an agent with no sense of who
          // it is answers as nobody — but the whole point of creating first is that the user has
          // not described it yet. The conversation rewrites this, and until it does the text is
          // honest about being unwritten rather than inventing a personality.
          identity:
            `${name} is a new agent. Its purpose has not been written yet — the conversation ` +
            `that follows defines what it does, how it speaks, and what it refuses.`,
        })
        // WHERE IT LANDED, kept for the preamble. A signed-in caller's agents go into their own
        // account overlay, so this is the only thing that knows the real path — and without it
        // the model is told a relative one that resolves against its workspace and fails.
        const dir = String((made as any)?.details?.dir || '')
        // The roster arrives by `agents.changed`, which create_agent broadcasts. Scope the new
        // conversation to it once it lands, so the inspector points at what was just made.
        newSession()
        setPendingScope({ id, dir })
        if (seed) void sendMessage(seed)
      } catch (e) {
        // SURFACED. A creation that failed silently leaves the user looking at an empty chat,
        // believing they are building something that does not exist.
        setCreateError(String((e as Error)?.message || e))
      } finally {
        setCreating('')
      }
    },
    [client, newSession, sendMessage],
  )

  /* POINT THE CONVERSATION AT THE NEW AGENT once the roster has it.
   *
   *  Not done inline above: `setScope` takes the roster ROW — the agent's colour, tagline and app
   *  table, none of which the tool's result carries — and the roster arrives asynchronously on
   *  `agents.changed`. Scoping to a row that does not exist yet would leave the inspector aimed at
   *  nothing, which is exactly what it looks like when creation has failed. */
  useEffect(() => {
    if (!pendingScope) return
    const row = agents.find((a) => a.id === pendingScope.id)
    if (!row) return
    // The roster row plus the one fact the roster cannot carry.
    setScope(pendingScope.dir ? { ...row, dir: pendingScope.dir } : row)
    setPendingScope(null)
  }, [pendingScope, agents, setScope])

  // Does this agent COMPILE its window? `app/` at the top of its tree is the whole test: source
  // there, build output in ui/. A hand-written ui/ has nothing to build and is live on save.
  const compiles = files.rows.some((r) => r.name === 'app' && r.depth === 0 && r.kind === 'folder')

  /* The workspace pane beside the conversation. Preview is guarded on hasWindow as well as the
     tab — an agent can LOSE its window mid-session (the model rewrites agent.toml), and a
     preview of nothing should close itself rather than 404. Files needs only a subject. */
  const previewOpen = !!selected && hasWindow(selected) && chat.wsTab === 'preview'
  const filesOpen = !!selected && chat.wsTab === 'files'
  const capsOpen = !!selected && chat.wsTab === 'caps'
  const testOpen = !!selected && chat.wsTab === 'test'
  const paneOpen = previewOpen || filesOpen || capsOpen || testOpen

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
        client={client}
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
        onCreate={() => setBlueprint({})}
        onEdit={() => setStart({ mode: 'edit' })}
        onSettings={() => setView('settings')}
        onCredits={() => setView('credits')}
        onOrgs={() => setView('orgs')}
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
      {/* RELOADS THIS WINDOW when Agent Builder itself is rebuilt, so working on the builder
          stops meaning "close it and reopen it".

          IT NAMES ITSELF, and it has to. This is a HOST connection: `_scoped_event_allowed`
          filters `app.rebuilt` for agent-scoped windows, but a host one bypasses that policy and
          receives every agent's rebuild. Without the id, building any agent would reload the
          window you were building it in, mid-conversation. */}
      <LiveReload client={client ?? undefined} agentId={AGENT_ID} />

      <main className="main">
        {/* CREATION IS A REAL OPERATION NOW — files are written and a window is assembled — so it
            takes a moment and has to say so. A shell that simply does not respond for a second is
            a shell the user clicks again. */}
        {creating && (
          <div className="create-note">Creating {creating} and building its window…</div>
        )}
        {createError && (
          <div className="create-note bad">
            Could not create the agent: {createError}
          </div>
        )}
        {view === 'credits' ? (
          /* THE SAME PAGE THE ASSISTANT SHOWS, copied into _common/ and rendered here. Running out
             of credits is the one failure a user can fix themselves, so it is a place you can go
             rather than a dialog over the thing that just stopped. */
          <Credits agentId={AGENT_ID} />
        ) : view === 'orgs' ? (
          <OrgView />
        ) : view === 'settings' ? (
          /* A PAGE, not a modal. It was a modal so that configuring the thing did not close the
             conversation you opened it because of — but it is tabbed now, and a tab strip inside
             a floating card is a page pretending not to be one. The conversation is one click
             away in the rail and its tab is still open. */
          <SettingsView client={client} />
        ) : view === 'launchpad' ? (
          <Launchpad
            agents={openable(agents)}
            onEdit={(id) => {
              setView('chat')
              editAgent(id)
            }}
            onCreate={() => setBlueprint({})}
            onCreateShape={(shape) => setBlueprint({ shape })}
            onPreviewTemplate={(id) => setViewer(id)}
            onOpenChat={(key) => {
              setView('chat')
              void openChat(key)
            }}
            onSuggest={(text) => setBlueprint({ seed: text })}
            credits={credits}
            onCredits={() => setView('credits')}
            status={status}
            daemonVersion={daemonVersion}
          />
        ) : view === 'myagents' ? (
          /* SUPERSEDED by the launchpad above and no longer reachable from the rail. Left routed
             until the launchpad has grown the rest of the page — see the View type. */
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
              previewable={hasWindow(selected)}
              wsTab={chat.wsTab}
              onWsTab={setWsTab}
              onShip={() => setShip(true)}
            />
        {openTabs.length === 0 ? (
          /* NOTHING OPEN. Not a blank composer pretending to be a conversation — conversations
             come through the launchpad's doors (create, or pick an agent), and this says so. */
          <section className="stage">
            <div className="chat-none">
              <p className="chat-none-title">Nothing open</p>
              <p className="chat-none-sub">
                Conversations start from the Launchpad — pick an agent to work on, or create one.
              </p>
              <button className="prime-btn" onClick={() => setView('launchpad')}>
                Open the Launchpad
              </button>
            </div>
          </section>
        ) : (
        <section className={`stage ${paneOpen ? 'stage--split' : ''}`}>
          {/* The conversation column — the whole stage when no pane is open (the old layout,
              exactly), a 400px column beside the live window when one is. */}
          <div className="stage-chat">
            {empty ? (
              /* NOTHING SAID YET: the input in the MIDDLE of the page rather than pinned to the
                 bottom. The Start card and the suggestions that used to sit around it moved to
                 the Launchpad — which is HOME now, and where "what shall we start" belongs. An
                 empty conversation is just a conversation that has not started. */
              <div className="chat-hero">
                <div className="chat-hero-composer">{composer}</div>
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
          </div>

          {previewOpen && selected && (
            <WorkspacePreview client={client} agent={selected} compiles={compiles} />
          )}
          {filesOpen && selected && (
            <WorkspaceFiles client={client} agent={selected} files={files} />
          )}
          {capsOpen && selected && (
            <WorkspaceCapabilities client={client} agent={selected} files={files} />
          )}
          {testOpen && selected && <WorkspaceTestDrive agent={selected} />}
        </section>
        )}
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

      {/* The create wizard, full-bleed over the shell. Mounted only while open, like the
          dialog it replaces; createAgent closes it the way it closed the dialog. */}
      {blueprint && (
        <Blueprint
          seed={blueprint.seed}
          initialShape={blueprint.shape}
          onCreate={createAgent}
          onClose={() => setBlueprint(null)}
        />
      )}

      {/* Ship: the inspector's verbs as a screen. The inspector keeps its copies until this
          one has earned the retirement. */}
      {ship && selected && (
        <ShipScreen
          client={client}
          agent={selected}
          authorLabel={who.known && who.signedIn ? who.label : ''}
          onClose={() => setShip(false)}
        />
      )}

      {/* Full-screen template walkthrough. "Use this template" hands the pick straight to the
          Blueprint — looking never creates anything. */}
      {viewer && (
        <TemplateViewer
          templateId={viewer}
          onUse={(id) => {
            setViewer(null)
            setBlueprint({ shape: id })
          }}
          onClose={() => setViewer(null)}
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
