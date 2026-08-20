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

import { mountSignInGate } from '@agentd/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useAgentFiles } from './agentd/agent-files'
import { buildAndOpen, hasWindow } from './agentd/app-window'
import { useChat } from './agentd/chat'
import { useClient } from './agentd/client'
import { usePlatform, useRestartDaemon, useWhoAmI } from './agentd/platform'
import { openable, type AgentRow } from './agentd/roster'
import { useAgents } from './agentd/roster'
import { useSessions } from './agentd/sessions'
import { Composer } from './components/Composer'
import { Inspector } from './components/Inspector'
import { PlanPanel } from './components/PlanPanel'
import { Rail } from './components/Rail'
import { CreditsModal } from './components/CreditsModal'
import { MyAgentsView } from './components/MyAgentsView'
import { SettingsModal } from './components/settings/SettingsModal'
import { StartModal, type StartMode } from './components/StartModal'
import { Thread } from './components/Thread'
import { Topbar } from './components/Topbar'

export default function App() {
  const { client, status } = useClient()
  const ready = status === 'open'

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [creditsOpen, setCreditsOpen] = useState(false)
  // WHICH SCREEN the stage is showing. Two, and only two: the conversation you are having,
  // and the shelf of what you have built. Everything else in this window is a modal, which is
  // the shape that does not close the thing you opened it because of (see SettingsModal).
  const [view, setView] = useState<'chat' | 'myagents'>('chat')
  const [selected, setSelected] = useState<AgentRow | null>(null)
  const [railOpen, setRailOpen] = useState(true)
  const [panelOpen, setPanelOpen] = useState(true)
  const [daemonVersion, setDaemonVersion] = useState('')
  // Which start dialog is open, and the suggestion that opened it. One piece of state, because
  // "create" and "edit" are two questions asked by one screen and never both at once.
  const [start, setStart] = useState<{ mode: StartMode; seed?: string } | null>(null)
  const [windowError, setWindowError] = useState('')
  const [opening, setOpening] = useState(false)

  const who = useWhoAmI(client, status)
  // A SECOND instance of this hook lives in SettingsView, deliberately. Each holds its own
  // copy of one immutable-ish fact and costs one status call; hoisting it to share would mean
  // threading platform state through the whole tree to save a request at boot. The settings
  // modal is mounted only while open, so it re-reads after a sign-out from here and the two
  // cannot drift where anyone can see it.
  const platform = usePlatform(client)
  const daemon = useRestartDaemon(client)

  // An agent that did not exist a moment ago was just BUILT — in this window, by this
  // conversation. Focus it, because watching its files appear is what the inspector is for and
  // making the user go and pick it would be asking them to find what they just asked for. Only
  // when nothing is focused: it must never steal the panel from an agent already being worked on.
  const { agents } = useAgents(client, ready, (born) => setSelected((prev) => prev ?? born))
  const { chats } = useSessions(client, ready)

  const files = useAgentFiles(client, selected?.id ?? null)
  // Read through a ref inside the chat callback: the subscription is opened once, and a stale
  // closure would keep refreshing the tree of whichever agent was selected when it was created.
  const refreshFiles = useRef(files.refresh)
  refreshFiles.current = files.refresh

  const chat = useChat(client, {
    onToolDone: () => void refreshFiles.current(),
  })

  // Boot, on the first open — the same order and the same place the vanilla window used.
  const booted = useRef(false)
  useEffect(() => {
    if (!ready || booted.current) return
    booted.current = true
    void (async () => {
      try {
        // Renders NOTHING on a BYOK build, when this device is already connected, or when a
        // stored session still works — so it is safe to call unconditionally.
        await mountSignInGate({ client })
      } catch (e) {
        // The daemon itself is unreachable. Not fatal: the chat surface reports that too, and
        // blocking the whole window on a status probe would hide the better message.
        console.warn('[sign-in]', (e as Error)?.message || e)
      }
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
      chat.reset()
      setSelected(agent) // the inspector follows the conversation
      chat.setScope(agent) // and the model is told what it is looking at
    },
    [agents, chat],
  )

  /** Start building something new, having answered the one question worth asking first. */
  const createAgent = useCallback(
    (window: boolean, seed?: string) => {
      setStart(null)
      setSelected(null) // there is nothing to inspect until it exists
      chat.reset()
      chat.setIntent({ window })
      if (seed) void chat.send(seed)
    },
    [chat],
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
      setSelected(null)
      const id = await chat.open(key)
      if (!id) return
      const agent = agents.find((a) => a.id === id)
      if (agent) setSelected(agent)
    },
    [chat, agents],
  )

  // NOTHING IN FOCUS -> NO PANEL. Not an empty panel with a placeholder in it: there is no
  // question for the panel to answer yet, and a column of dashes is furniture.
  const shellClass = ['shell', railOpen ? '' : 'no-rail', selected && panelOpen ? '' : 'no-panel']
    .filter(Boolean)
    .join(' ')

  return (
    <div className={shellClass}>
      <Rail
        open={railOpen}
        onToggle={() => setRailOpen((v) => !v)}
        chats={chats}
        openKey={chat.sessionKey}
        onOpenChat={(key) => {
          setView('chat')
          void openChat(key)
        }}
        agents={openable(agents)}
        selectedId={selected?.id || ''}
        // Picking an agent in the sidebar IS editing it — the same call the picker makes. A
        // sidebar row that only highlighted something would be a click that does nothing.
        onPickAgent={(id) => {
          setView('chat')
          editAgent(id)
        }}
        onCreate={() => setStart({ mode: 'create' })}
        onEdit={() => setStart({ mode: 'edit' })}
        onSettings={() => setSettingsOpen(true)}
        onCredits={() => setCreditsOpen(true)}
        onMyAgents={() => setView('myagents')}
        view={view}
        auth={platform.auth}
        authError={platform.error}
        onSignIn={platform.signIn}
        onSignOut={platform.signOut}
        status={status}
        daemonVersion={daemonVersion}
      />

      <main className="main">
        <Topbar
          agent={selected}
          who={who}
          railOpen={railOpen}
          onToggleRail={() => setRailOpen((v) => !v)}
          onRestart={() => void daemon.restart()}
          restarting={daemon.busy}
          restartNote={daemon.note}
          canTogglePanel={!!selected}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen((v) => !v)}
        />

        {view === 'myagents' ? (
          <MyAgentsView
            agents={openable(agents)}
            onEdit={(id) => {
              setView('chat')
              editAgent(id)
            }}
          />
        ) : (
        <section className="stage">
          <Thread
            items={chat.items}
            running={chat.running}
            hasAgents={openable(agents).length > 0}
            onCreate={() => setStart({ mode: 'create' })}
            onEdit={() => setStart({ mode: 'edit' })}
            // A suggestion says WHAT to build; the dialog is still HOW. Routing it through the
            // same door is what stops the most-taken path being the one that skips the question.
            onSuggest={(text) => setStart({ mode: 'create', seed: text })}
          />
          {chat.plan && <PlanPanel plan={chat.plan} />}
          <Composer
            running={chat.running}
            pending={chat.pending}
            onSend={(text) => void chat.send(text)}
            onAbort={() => void chat.abort()}
            onFiles={(list) => void chat.addFiles(list)}
            onRemoveFile={chat.removeFile}
            onOpenWindow={hasWindow(selected) ? () => void openWindow() : undefined}
            openWindowLabel={opening ? 'Building…' : undefined}
            openWindowBusy={opening}
          />
          {windowError && (
            <div className="composer-error" role="alert">
              {windowError}
              <button className="icon-btn" onClick={() => setWindowError('')} title="Dismiss">
                ✕
              </button>
            </div>
          )}
        </section>
        )}
      </main>

      <Inspector
        agent={selected}
        client={client}
        files={files}
        onChanged={() => void files.refresh()}
      />

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

      {settingsOpen && <SettingsModal client={client} onClose={() => setSettingsOpen(false)} />}
      {creditsOpen && <CreditsModal onClose={() => setCreditsOpen(false)} />}
    </div>
  )
}
