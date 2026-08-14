/* Agent Builder — the shell, and the wiring between its three regions.
 *
 *   rail       which conversation, and which view
 *   main       the conversation, or the settings page
 *   inspector  the agent being built: its files, and the three things you can do to it
 *
 * FOCUS BELONGS TO A CONVERSATION. What the inspector points at is decided when a chat starts —
 * in the hero, or by the agent that chat just created — and never re-pointed mid-thread, because
 * a panel you can aim somewhere else is a panel that can disagree with the conversation beside
 * it. To work on something else, start a new chat.
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
import { Rail } from './components/Rail'
import { SettingsView } from './components/settings/SettingsView'
import { Thread } from './components/Thread'
import { Topbar } from './components/Topbar'

export type View = 'build' | 'settings'

export default function App() {
  const { client, status } = useClient()
  const ready = status === 'open'

  const [view, setView] = useState<View>('build')
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
    // The conversation CHANGED — a new one, or a saved one reopened. Focus goes with it: carrying
    // the last chat's agent into the next one is the drift this panel is not allowed to have.
    onSession: () => setSelected(null),
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

  const newChat = useCallback(() => {
    setView('build')
    chat.reset()
  }, [chat])

  // NOTHING IN FOCUS -> NO PANEL. Not an empty panel with a placeholder in it: there is no
  // question for the panel to answer yet, and a column of dashes is furniture.
  const shellClass = ['shell', railOpen ? '' : 'no-rail', selected && panelOpen ? '' : 'no-panel']
    .filter(Boolean)
    .join(' ')

  return (
    <>
      <div className="aurora" aria-hidden="true">
        <i />
        <i />
        <i />
      </div>

      <div className={shellClass}>
        <Rail
          open={railOpen}
          onToggle={() => setRailOpen((v) => !v)}
          view={view}
          onView={setView}
          chats={chats}
          openKey={chat.sessionKey}
          onOpenChat={(key) => void chat.open(key)}
          onNewChat={newChat}
          status={status}
          daemonVersion={daemonVersion}
        />

        <main className="main">
          <Topbar
            root={view === 'settings' ? 'Settings' : 'Agent Builder'}
            leaf={selected ? `building ${selected.name || selected.id}` : ''}
            who={who}
            canTogglePanel={!!selected}
            onTogglePanel={() => setPanelOpen((v) => !v)}
          />

          <section className="view" id="view-build" hidden={view !== 'build'}>
            <Thread
              items={chat.items}
              agents={openable(agents)}
              onOpenAgent={openAgent}
              onSuggest={(text) => void chat.send(text)}
            />
            <Composer
              running={chat.running}
              pending={chat.pending}
              onSend={(text) => void chat.send(text)}
              onAbort={() => void chat.abort()}
              onFiles={(list) => void chat.addFiles(list)}
              onRemoveFile={chat.removeFile}
            />
          </section>

          {/* Mounted only while it is shown, which is what loads it: the vanilla window called
              Settings.load() on every switch to this view, and mounting does the same thing
              without a second place to remember it. */}
          {view === 'settings' && (
            <section className="view" id="view-settings">
              <SettingsView client={client} />
            </section>
          )}
        </main>

        <Inspector
          agent={selected}
          client={client}
          files={files}
          onChanged={() => void files.refresh()}
        />
      </div>
    </>
  )
}
