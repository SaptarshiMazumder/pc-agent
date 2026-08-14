/* Comfy Smith — a CHAT agent that drives a machine it does not own.
 *
 * SIX DESTINATIONS, ONE WINDOW.
 *
 *   Chat                  the conversation, plus the workflow it is currently about
 *   Workflows             everything it has built, and the images those produced
 *   Server/Models/Nodes   direct tool calls against the remote ComfyUI — no model, no tokens
 *   Settings             account, billing mode, where the server is, the credential to reach it
 *
 * Settings is not an afterthought. This agent fails in exactly one way — it cannot reach the
 * ComfyUI server — and an agent that fails for a missing field, in a window with no way to set
 * that field, is indistinguishable from an agent that is broken. So the sidebar carries a dot
 * when a required field is empty, from the first render, before anything has gone wrong.
 *
 * The workflow pane sits BESIDE the chat rather than under it because the artifact is the point:
 * a file the user imports. Left in the transcript it has to be scrolled back to and guessed
 * about. It refreshes whenever a tool that writes files finishes, so it can never show something
 * older than the conversation.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  useAuth,
  useChat,
  useClient,
  useMcpStatus,
  useSessions,
  useSettings,
  useTool,
  useWhenOpen,
  useWorkspace,
} from './agentd'
import { ArtifactsView } from './components/ArtifactsView'
import { ChatPane } from './components/ChatPane'
import { HistoryPanel } from './components/HistoryPanel'
import { InspectorView, type Inspector } from './components/InspectorView'
import { SettingsView } from './components/SettingsView'
import { Sidebar, type View } from './components/Sidebar'
import { WorkflowPanel, type Workflow } from './components/WorkflowPanel'

const SUGGESTIONS = [
  {
    title: 'See the server',
    body: 'What GPU is on my ComfyUI, how much VRAM is free, and what is installed?',
    cue: 'Inspect',
  },
  {
    title: 'Build and run',
    body: 'Build a text-to-image workflow from what is installed, then actually run it.',
    cue: 'Build',
  },
  {
    title: 'Fix a render',
    body: 'This came out blurry. Here is the workflow — find what is wrong and re-run it.',
    cue: 'Debug',
  },
]

/** Tools that can change a file in the workspace. Refreshing on EVERY tool would re-read the
 *  workspace after each web search for nothing. */
const WRITES_FILES = (name: string) =>
  name === 'write' || name === 'edit' || name === 'run_workflow' || name.startsWith('validate')

export default function App() {
  const { client, status } = useClient()
  const [view, setView] = useState<View>('chat')
  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [changed, setChanged] = useState(0)
  const listWorkspace = useWorkspace(client)
  const invoke = useTool(client)
  const panes = usePanes()

  const settings = useSettings(client)
  const mcp = useMcpStatus(client)
  const account = useAuth(client)

  /** Read the newest workflow the agent has written. Called on connect and after any tool that
   *  could have changed it — never on a timer, because nothing changes unless the agent acts. */
  const refresh = useCallback(async () => {
    try {
      const files = await listWorkspace('workflows')
      const newest = files.filter((f) => f.name.endsWith('.json')).pop()
      if (!newest) return
      // Read it with the agent's OWN `read` tool rather than GET /file. Same bytes, but this
      // path is already authorised by the connection — /file wants the token and an absolute
      // path, which means threading credentials through the UI for no benefit.
      const json = await invoke('read', { path: newest.path })
      setWorkflow({ name: newest.name, path: newest.path, json })
    } catch {
      // A missing workspace/workflows dir is the normal state before the first build. The panel
      // renders its own empty state; a thrown error here would blank the whole app instead.
    }
  }, [listWorkspace, invoke])

  const { turns, busy, ask, stop, reset, open, sessionKey } = useChat(client, {
    onToolDone: (name) => {
      if (WRITES_FILES(name)) {
        void refresh()
        setChanged((n) => n + 1) // the artifacts view re-lists
      }
    },
  })

  useWhenOpen(client, refresh)

  // Re-listed whenever the thread changes, so a conversation started a moment ago is in the
  // list rather than missing until the next reload.
  const { sessions, rename, remove, fork } = useSessions(client, sessionKey + String(busy))

  /** Fork and LAND IN THE COPY. A fork that silently adds a row to the list is indistinguishable
   *  from one that did nothing — the whole point is to carry on somewhere new. */
  const forkInto = useCallback(
    async (key: string) => {
      open(await fork(key))
    },
    [fork, open],
  )

  // A required setting with no value is the failure this agent has before it has any others.
  const missingRequired = (settings.data?.settings ?? []).some(
    (f) => f.required && !settings.data?.env?.[f.key],
  )

  // HISTORY IS A CHOICE, NOT A BREAKPOINT.
  //
  // It used to appear only above a width threshold, with a fallback button below it. Two pixels
  // of window either side of that line changed which of the two you got, so on an ordinary
  // window the history simply was not there and nothing on screen said why. A layout rule that
  // silently removes a feature is indistinguishable from a missing feature.
  //
  // Now the button is always present and the width only decides HOW the panel appears: docked
  // beside the conversation when there is room, over it when there is not.
  const [historyOpen, setHistoryOpen] = useHistoryPreference()

  return (
    <div className="shell">
      <Sidebar
        view={view}
        onView={setView}
        status={status}
        alert={missingRequired}
        auth={account.auth}
        onSignOut={account.signOut}
      />

      <div className="main">
        <header className="topbar">
          <h1 className="greeting">{title(view)}</h1>
          <span className="grow" />
          <ServerChip url={settings.data?.settingsValues?.COMFY_URL} onOpen={() => setView('settings')} />
          {view === 'chat' && (
            <>
              <button
                className={`ghost ${historyOpen ? 'on' : ''}`}
                onClick={() => setHistoryOpen(!historyOpen)}
              >
                History
              </button>
              {/* Fork THIS conversation, where you are already in it — the moment you want a
                  branch is mid-thread, not while browsing a list. */}
              <button
                className="ghost"
                onClick={() => void forkInto(sessionKey)}
                disabled={busy || !turns.length}
                title="Copy this conversation and its context into a new one"
              >
                Fork
              </button>
              <button className="ghost" onClick={reset} disabled={busy}>
                New chat
              </button>
            </>
          )}
        </header>

        <main className={`stage ${view}`}>
          {view === 'chat' && (
            <>
              <ChatPane
                turns={turns}
                busy={busy}
                suggestions={SUGGESTIONS}
                onAsk={ask}
                onStop={() => void stop()}
              />
              {panes.workflow && (
                <section className="pane side">
                  <WorkflowPanel workflow={workflow} invoke={invoke} />
                </section>
              )}
              {historyOpen && (
                <HistoryPanel
                  floating={!panes.history}
                  sessions={sessions}
                  current={sessionKey}
                  onOpen={(key) => {
                    open(key)
                    if (!panes.history) setHistoryOpen(false)
                  }}
                  onRename={rename}
                  onDelete={remove}
                  onFork={forkInto}
                  onNew={() => {
                    reset()
                    if (!panes.history) setHistoryOpen(false)
                  }}
                  onClose={panes.history ? undefined : () => setHistoryOpen(false)}
                />
              )}
            </>
          )}

          {view === 'artifacts' && (
            <ArtifactsView
              client={client}
              listWorkspace={listWorkspace}
              invoke={invoke}
              refreshKey={changed}
            />
          )}

          {(view === 'server' || view === 'models' || view === 'nodes') && (
            <InspectorView kind={view as Inspector} invoke={invoke} connected={status === 'open'} />
          )}

          {view === 'settings' && (
            <SettingsView
              data={settings.data}
              error={settings.error}
              onSave={settings.save}
              onTest={() => invoke('comfy_server')}
              mcp={mcp}
              auth={account.auth}
              authBusy={account.busy}
              authError={account.error}
              onSignOut={account.signOut}
              onMode={account.chooseMode}
            />
          )}
        </main>
      </div>
    </div>
  )
}

/** The server this window is pointed at, in the top bar. It is the one fact that changes what
 *  every answer means, and it is otherwise buried two clicks away in Settings. */
function ServerChip({ url, onOpen }: { url?: string; onOpen: () => void }) {
  if (!url) {
    return (
      <button className="chip-warn" onClick={onOpen}>
        No server set
      </button>
    )
  }
  return (
    <button className="chip-quiet" onClick={onOpen} title={url}>
      {url.replace(/^https?:\/\//, '')}
    </button>
  )
}

function title(view: View): string {
  switch (view) {
    case 'chat':
      return 'Comfy Smith'
    case 'artifacts':
      return 'Workflows'
    case 'server':
      return 'Server'
    case 'models':
      return 'Models'
    case 'nodes':
      return 'Nodes'
    case 'settings':
      return 'Settings'
  }
}

/** Panes drop ONE AT A TIME, widest-first.
 *
 *  A single breakpoint that hides both is how a 1080px window — an ordinary window — ends up
 *  showing a bare transcript with no history and no artifact, which reads as features that were
 *  never built rather than features that did not fit.
 *
 *  So: the workflow pane goes first, because the Workflows view reaches everything it showed.
 *  History survives down to a genuinely narrow window, because nothing else lists conversations.
 */
function usePanes(): { workflow: boolean; history: boolean } {
  const [width, setWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  // `history` here means DOCKED, not visible — below it the same panel floats, so nothing is
  // lost at any width. The workflow pane has no float form: the Workflows view is its fallback.
  return { workflow: width >= 1400, history: width >= 940 }
}

/** Whether the history column is showing, remembered across launches.
 *
 *  A panel the user closed should stay closed, and one they opened should come back — otherwise
 *  the first thing they do in every session is re-open it. */
function useHistoryPreference(): [boolean, (value: boolean) => void] {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem('agentd:history') !== 'closed'
    } catch {
      return true // storage disabled: default to showing it, never to hiding it
    }
  })
  const set = useCallback((value: boolean) => {
    setOpen(value)
    try {
      localStorage.setItem('agentd:history', value ? 'open' : 'closed')
    } catch {
      /* non-fatal — the choice just will not survive a restart */
    }
  }, [])
  return [open, set]
}
