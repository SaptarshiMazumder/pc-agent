/* Paper Pile — a WORKBENCH, not a chat window.
 *
 * The user arrives with a pile, not a question, so the window opens on the work: a drop zone, a
 * folder to scan, and the library. Chat is one section among several, kept because "which of
 * these disagree?" is a question a list cannot answer.
 *
 * THE SHELL IS THE POINT. An agent that ships its own `entry` REPLACES the built-in window, and
 * inherits responsibility for everything that window provided: its conversations, its files, and
 * its settings. Skip them and the agent looks broken in ways that are invisible from inside the
 * code — a declared setting with nowhere to type it silently never takes effect.
 *
 * ONE NAV, NOT TWO. This started with an icon rail beside a text list of the SAME five sections.
 * Two controls for one choice read as two unrelated things, and the eye has to check both to
 * find out where it is. Icon and label belong on one row.
 *
 * EVERYTHING HERE IS ONE WEBSOCKET. The library list, the link graph and the search are
 * `tools.invoke` (no model, no tokens); history, settings and files are plain RPCs. Only the Ask
 * section spends a turn.
 */

import { useCallback, useEffect, useState } from 'react'
import { useChat, useClient, useFiles, useSessions, useSettings, useTool } from './agentd'
import { useQueue } from './useQueue'
import { Dropzone } from './components/Dropzone'
import { Queue } from './components/Queue'
import { Library, type Doc, type IndexedFile } from './components/Library'
import { Reader } from './components/Reader'
import { Connections } from './components/Connections'
import { Ask } from './components/Ask'
import { Artifacts } from './components/Artifacts'
import { Settings } from './components/Settings'
import { History } from './components/History'
import { FolderButton } from './components/FolderButton'
import Credits from './common/credits/Credits'

type View = 'library' | 'links' | 'ask' | 'files' | 'credits' | 'settings'

const SECTIONS: Array<{ id: View; label: string; icon: string }> = [
  { id: 'library', label: 'Library', icon: '▤' },
  { id: 'links', label: 'Connections', icon: '⁂' },
  { id: 'ask', label: 'Ask', icon: '✳' },
  { id: 'files', label: 'Artifacts', icon: '❑' },
  // Credits ABOVE Settings and a section of its own: topping up is what a user comes
  // looking for when a run stops, and settings is where you go to change how it works.
  { id: 'credits', label: 'Credits', icon: '◈' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
]

export default function App() {
  const { client, status } = useClient()
  const invoke = useTool(client)
  const [docs, setDocs] = useState<Doc[]>([])
  const [indexedFiles, setIndexedFiles] = useState<IndexedFile[]>([])
  const [selected, setSelected] = useState<Doc | null>(null)
  const [view, setView] = useState<View>('library')
  const [error, setError] = useState('')

  const ready = status === 'open'
  const sessions = useSessions(client, ready)
  const settings = useSettings(client, ready)
  const files = useFiles(client, ready)

  const loadLibrary = useCallback(async () => {
    try {
      const [notesRaw, inventoryRaw] = await Promise.all([
        invoke('library_index', {}),
        invoke('library_inventory', {}),
      ])
      // Notes and indexed sources are different stores. Showing only notes made generated summaries
      // look like the RAG inventory, which is exactly the ambiguity this screen must avoid.
      const notes = notesRaw.trim().startsWith('{') ? JSON.parse(notesRaw) : { documents: [] }
      const inventory = inventoryRaw.trim().startsWith('{') ? JSON.parse(inventoryRaw) : { files: [] }
      setDocs(notes.documents ?? [])
      setIndexedFiles(inventory.files ?? [])
      setError('')
    } catch (e) {
      setError(`could not read the library: ${(e as Error)?.message ?? e}`)
    }
  }, [invoke])

  const { turns, busy, ask, abort, reset, resume } = useChat(client, {
    onToolDone: (name) => {
      // Whatever the agent changed in chat must show up in the panels. Without this the screen
      // shows the state from before it acted, and the user is looking at a lie.
      if (name === 'write' || name === 'edit' || name === 'library_put') void loadLibrary()
      if (name === 'write' || name === 'library_put') void files.reload()
    },
  })

  const queue = useQueue(client, ask, loadLibrary, abort)

  // Switching sections closes the open note. Leaving it open means clicking "Connections" shows
  // the document you were reading, which reads as a broken tab.
  const go = (next: View) => {
    setView(next)
    setSelected(null)
  }

  useEffect(() => {
    if (ready) void loadLibrary()
  }, [ready, loadLibrary])


  /** Fork, then LAND in the copy — a fork you do not end up in looks like nothing happened. */
  const forkThread = async (id: string) => {
    const copy = await sessions.fork(id)
    if (copy) await openThread(copy)
  }

  /** Does that folder exist, and does it hold anything worth ingesting?
   *
   *  Answered by `library_browse`, which is the same code path the agent uses — so a green result
   *  here means the agent will genuinely find it, not that the string looked plausible. */
  const testInbox = async (_key: string, value: string): Promise<string> => {
    try {
      const raw = await invoke('library_browse', { path: value })
      if (!raw.trim().startsWith('{')) return raw.trim()
      const listing = JSON.parse(raw)
      const here = listing.documents_here ?? 0
      const subs = (listing.folders ?? []).length
      return here === 0 && subs === 0
        ? `Found it, but there are no documents in ${listing.path}.`
        : `Found ${here} document${here === 1 ? '' : 's'} here and ${subs} sub-folder${
            subs === 1 ? '' : 's'
          } in ${listing.path}.`
    } catch (e) {
      return String((e as Error)?.message ?? e)
    }
  }

  const openThread = async (id: string) => {
    const messages = await sessions.history(id)
    resume(id, messages)
    go('ask')
  }

  return (
    <div className="shell">
      <aside className="side">
        <h1 className="brand">
          <span className="mark">▤</span> Paper Pile
        </h1>

        <nav>
          <ul className="nav">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <button className={view === s.id ? 'on' : ''} onClick={() => go(s.id)}>
                  <span className="nav-icon" aria-hidden="true">
                    {s.icon}
                  </span>
                  <span className="nav-label">{s.label}</span>
                  {s.id === 'library' && indexedFiles.length > 0 && (
                    <span className="count">{indexedFiles.length}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="side-block">
          <h2>Add documents</h2>
          <Dropzone onFiles={queue.add} />
          <FolderButton
            disabled={!ready}
            onFiles={(docs) => {
              // Straight into the queue the drop zone already uses: one row per document, per-item
              // state, one failure never stopping the rest. "Selected" has to be VISIBLE, and a
              // list you can watch is the only honest version of that.
              queue.add(docs)
            }}
          />
        </div>

        <Queue
          items={queue.items}
          onRun={queue.run}
          onStop={() => void queue.stop()}
          onClear={queue.clearFinished}
          disabled={!ready}
        />

        <div className="side-foot">
          <span className={`dot ${status}`} />
          <span>{status === 'open' ? 'connected' : status}</span>
        </div>
      </aside>

      <main className="main">
        {error && <p className="err">{error}</p>}

        {selected && view !== 'ask' ? (
          <Reader doc={selected} invoke={invoke} onBack={() => setSelected(null)} />
        ) : view === 'library' ? (
          <Library docs={docs} indexedFiles={indexedFiles} invoke={invoke} onOpen={setSelected} />
        ) : view === 'links' ? (
          <Connections docs={docs} invoke={invoke} onOpen={setSelected} />
        ) : view === 'files' ? (
          <Artifacts
            entries={files.entries}
            error={files.error}
            path={files.path}
            onOpen={(rel) => void ask(`Show me what is in ${rel}.`)}
            onDelete={(rel) => void files.remove(rel)}
            onRefresh={(p) => void files.reload(p)}
          />
        ) : view === 'credits' ? (
          <Credits />
        ) : view === 'settings' ? (
          <Settings
            fields={settings.fields}
            values={settings.values}
            present={settings.present}
            error={settings.error}
            onSave={settings.save}
            onTest={testInbox}
          />
        ) : (
          <Ask turns={turns} busy={busy} onSend={ask} onStop={() => void abort()} />
        )}
      </main>

      {view === 'ask' && (
        <History
          rows={sessions.rows}
          error={sessions.error}
          activeId=""
          onOpen={(id) => void openThread(id)}
          onRename={(id, title) => void sessions.rename(id, title)}
          onDelete={(id) => void sessions.remove(id)}
          onFork={(id) => void forkThread(id)}
          onNew={() => {
            reset()
            void sessions.reload()
          }}
        />
      )}
    </div>
  )
}
