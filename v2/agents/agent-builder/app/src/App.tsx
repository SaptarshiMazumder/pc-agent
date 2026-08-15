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
import { useChat } from './agentd/chat'
import { useClient } from './agentd/client'
import { useWhoAmI } from './agentd/platform'
import { openable, type AgentRow } from './agentd/roster'
import { useAgents } from './agentd/roster'
import { useSessions } from './agentd/sessions'
import { Composer } from './components/Composer'
import { Inspector } from './components/Inspector'
import { PlanPanel } from './components/PlanPanel'
import { Rail } from './components/Rail'
import { SettingsModal } from './components/settings/SettingsModal'
import { Thread } from './components/Thread'
import { Topbar } from './components/Topbar'

export default function App() {
  const { client, status } = useClient()
  const ready = status === 'open'

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [selected, setSelected] = useState<AgentRow | null>(null)
  const [railOpen, setRailOpen] = useState(true)
  const [panelOpen, setPanelOpen] = useState(true)
  const [daemonVersion, setDaemonVersion] = useState('')

  const who = useWhoAmI(client, status)

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

  const openAgent = useCallback(
    (id: string) => {
      const agent = agents.find((a) => a.id === id)
      if (!agent) return
      setSelected(agent) // the inspector follows the conversation
      chat.setScope(agent) // and the model is told what it is looking at
    },
    [agents, chat],
  )

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

  const newChat = useCallback(() => {
    setSelected(null) // a new chat is about nothing until it is told
    chat.reset()
  }, [chat])

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
        onOpenChat={(key) => void openChat(key)}
        onNewChat={newChat}
        onSettings={() => setSettingsOpen(true)}
        status={status}
        daemonVersion={daemonVersion}
      />

      <main className="main">
        <Topbar
          agent={selected}
          who={who}
          railOpen={railOpen}
          onToggleRail={() => setRailOpen((v) => !v)}
          canTogglePanel={!!selected}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen((v) => !v)}
        />

        <section className="stage">
          <Thread
            items={chat.items}
            running={chat.running}
            agents={openable(agents)}
            onOpenAgent={openAgent}
            onSuggest={(text) => void chat.send(text)}
          />
          {chat.plan && <PlanPanel plan={chat.plan} />}
          <Composer
            running={chat.running}
            pending={chat.pending}
            onSend={(text) => void chat.send(text)}
            onAbort={() => void chat.abort()}
            onFiles={(list) => void chat.addFiles(list)}
            onRemoveFile={chat.removeFile}
          />
        </section>
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
      {settingsOpen && <SettingsModal client={client} onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
