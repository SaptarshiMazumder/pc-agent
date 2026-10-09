/* Ad Studio's window: a rail, then the chat beside the studio.
 *
 *   rail  | chat (thread + composer)  ┆  studio (the campaign's steps, every result, the cast)
 *
 * THE STUDIO SHOWS; WHAT COSTS GOES THROUGH THE CHAT. The studio reads through the agent's
 * read-only tools and picks results directly (free); every generation is sent as a chat message
 * carrying the exact call, so the thread records what was asked for. (agentd/campaigns.ts)
 *
 * The shared screens (credits, organizations, settings, sign-in) are the platform's, under
 * src/common/, and are not edited here.
 */

import { useEffect } from 'react'
import { BookOpen, Images, Megaphone, Users } from 'lucide-react'

import { campaignStatus, selectionBlock, START } from './agentd/campaigns'
import { AGENT_ID, useClient } from './agentd/client'
import { useCredits } from './agentd/credits'
import { handleRunEvent } from './agentd/run-events'
import { MAX_FILES } from './agentd/chat'
import { useRun } from './agentd/run'
import { listSessions, loadHistory } from './agentd/sessions'
import { useApp, useSession } from './state/store'

import { Composer } from './components/Composer'
import { Sidebar } from './components/Sidebar'
import { Thread } from './components/Thread'
import { CampaignsPage } from './pages/CampaignsPage'
import { CastPage } from './pages/CastPage'
import { RecipesPage } from './pages/RecipesPage'
import { ChatResizer } from './studio/ChatResizer'
import { MediaViewer } from './studio/MediaViewer'
import { NewAdOpening } from './studio/NewAdOpening'
import { PostOpening } from './studio/PostOpening'
import { PostsPage } from './pages/PostsPage'
import { POST_START, type Post } from './agentd/posts'
import { SelectionChips } from './studio/SelectionChips'
import { Studio } from './studio/Studio'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'
import { Settings } from './common/settings/Settings'

import './studio/studio.css'

const AGENT_NAME = 'Ad Studio'

export default function App() {
  const { client, status } = useClient()
  const connected = status === 'open'

  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const newSession = useApp((s) => s.newSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  const chats = useApp((s) => s.chats)
  const seedComposer = useApp((s) => s.seedComposer)
  const setStart = useApp((s) => s.setStart)
  const chatWidth = useApp((s) => s.chatWidth)
  const session = useSession()

  const { send, abort, addFiles, removeFile } = useRun(client)
  /* A typed message carries what the user selected, by exact path, so the agent acts on exactly
     those files. A message from a step panel already names its files in its call; it only clears the selection. */
  const sendTyped = (text: string) => {
    const { selection, clearSelection } = useApp.getState()
    void send(text + selectionBlock(selection))
    clearSelection()
  }
  const sendAnswer = (text: string) => {
    void send(text)
    useApp.getState().clearSelection()
  }
  const credits = useCredits(client!, session.running)
  const account = useAuth(client!)

  // One subscription, torn down on reconnect (a re-dial would otherwise stack handlers).
  useEffect(() => {
    if (!client) return
    const off = client.on('chat.event', (payload: any) => handleRunEvent(payload))
    return () => off()
  }, [client])

  // A reconnect does not mean the run died: ask, and re-attach if it is still going.
  useEffect(() => {
    if (!connected || !client) return
    const { sessions } = useApp.getState()
    for (const key of Object.keys(sessions)) {
      if (!sessions[key].running) continue
      void (async () => {
        let running = false
        try {
          const st = (await client.request('chat.status', { sessionKey: key })) as { running?: boolean }
          running = !!st?.running
        } catch {
          /* older daemon — assume the run is gone */
        }
        if (running) return
        const { sessions: now, patch, append } = useApp.getState()
        if (!now[key]?.running) return
        patch(key, { running: false })
        append(key, [
          {
            kind: 'system',
            tone: 'error',
            text: 'This run ended while the window was away — the conversation up to here is saved.',
            ts: Date.now(),
          },
        ])
      })()
    }
  }, [connected, client])

  useEffect(() => {
    if (!connected || !client) return
    if (!currentKey) newSession(false)
    void listSessions(client).then((rows) => useApp.getState().setChats(rows))
  }, [connected, client, currentKey, newSession])

  // A chat started from the Posts tab makes a post from a collection, not an ad.
  const postChat = useApp((s) => s.starts[currentKey]?.mode === 'post')

  /** A new ad started from the Recipes or Cast page: a new chat with that choice made; the studio
   *  offers the other one. */
  const newAdWith = (choice: { recipe?: string; cast?: string }, text: string) => {
    const key = newSession()
    setStart(key, choice)
    seedComposer(text + ' ')
  }
  const refreshChats = async () => {
    if (!client) return
    useApp.getState().setChats(await listSessions(client))
  }

  /** Open a campaign: in the chat that started it, or pinned into this one. */
  const openCampaign = async (id: string) => {
    if (!client) return
    const { campaign } = await campaignStatus(client, id)
    const home = campaign.session && chats.some((c) => c.sessionId === campaign.session) ? campaign.session : ''
    const st = useApp.getState()
    if (home) {
      st.openSession(home)
      const items = await loadHistory(client, home)
      if (items.length) useApp.getState().openSession(home, items)
    } else {
      st.pin(st.currentSessionKey, id)
      st.setView('chat')
    }
  }

  /** Open a post: in the chat that made it, or pinned into this one. */
  const openPost = async (post: Post) => {
    if (!client) return
    const st = useApp.getState()
    if (post.session && chats.some((c) => c.sessionId === post.session)) {
      st.openSession(post.session)
      const items = await loadHistory(client, post.session)
      if (items.length) useApp.getState().openSession(post.session, items)
    } else {
      st.pinPost(st.currentSessionKey, post.slug)
      st.setView('chat')
    }
  }

  if (account.wantsSignIn) return <SignIn product={AGENT_NAME} onDone={account.signedIn} />

  const openChat = chats.find((c) => c.sessionId === currentKey)
  const empty = session.items.length === 0
  const pct = session.usage && session.usage.limit > 0 ? Math.round(session.usage.pct) : null

  return (
    <div className="shell">
      <LiveReload client={client ?? undefined} />
      <Sidebar
        view={view}
        onView={setView}
        onNewChat={() => newSession()}
        onRefreshChats={refreshChats}
        account={account}
        client={client ?? undefined}
        status={status}
        name={AGENT_NAME}
        counts={{ credits: credits === null ? undefined : credits.toLocaleString() }}
        groupLabel="Studio"
        sharedGroupLabel="Account"
        extraDestinations={[
          { id: 'recipes', label: 'Recipes', icon: <BookOpen size={15} /> },
          { id: 'campaigns', label: 'Campaigns', icon: <Images size={15} /> },
          { id: 'cast', label: 'Cast', icon: <Users size={15} /> },
          { id: 'posts', label: 'Posts', icon: <Megaphone size={15} /> },
        ]}
      />

      <main className="main">
        {view === 'credits' ? (
          <Credits agentId={AGENT_ID} />
        ) : view === 'orgs' ? (
          <OrgView client={client ?? undefined} />
        ) : view === 'settings' ? (
          client && <Settings client={client} agentId={AGENT_ID} />
        ) : view === 'campaigns' ? (
          <CampaignsPage client={client} onOpen={(id) => void openCampaign(id)} />
        ) : view === 'recipes' ? (
          <RecipesPage client={client} onUse={(key, startText) => newAdWith({ recipe: key }, START.recipe(key, startText))} />
        ) : view === 'posts' ? (
          <PostsPage
            client={client}
            onPost={(c) => {
              const key = newSession()
              setStart(key, { mode: 'post', collection: c.slug })
              seedComposer(POST_START(c.name) + ' ')
            }}
            onNewPost={() => {
              const key = newSession()
              setStart(key, { mode: 'post' })
            }}
            onOpenPost={(p) => void openPost(p)}
          />
        ) : view === 'cast' ? (
          <CastPage
            client={client}
            onUse={(name) => newAdWith({ cast: name }, START.cast(name))}
            onNew={() => {
              newSession()
              seedComposer(START.newCastAlone)
            }}
          />
        ) : (
          <div className="st-cols">
            <section className="st-convo" style={{ width: chatWidth }}>
              <header className="convo-head">
                <h1 className="convo-title">{empty ? (postChat ? 'New post' : 'New ad') : openChat?.title || 'Ad'}</h1>
                {pct !== null && (
                  <span className="meter" title={`${session.usage!.used} of ${session.usage!.limit} tokens`}>
                    {pct}% context
                  </span>
                )}
              </header>

              {empty ? (
                postChat ? <PostOpening client={client} session={currentKey} /> : <NewAdOpening client={client} session={currentKey} />
              ) : (
                <Thread items={session.items} running={session.running} onSuggest={(p) => void send(p)} />
              )}

              <Composer
                running={session.running}
                pending={session.pending}
                onSend={sendTyped}
                above={<SelectionChips />}
                onAbort={() => void abort()}
                onFiles={(files) => void addFiles(files)}
                onRemoveFile={removeFile}
                credits={credits}
                onCredits={() => setView('credits')}
                maxFiles={MAX_FILES}
                connected={connected}
                model={session.usage?.model || ''}
                placeholder="Attach product photos, say who and where…"
                meter={null}
              />
            </section>

            <ChatResizer />

            <Studio client={client} connected={connected} session={currentKey} running={session.running} onAnswer={sendAnswer} />
          </div>
        )}
      </main>
      <MediaViewer />
    </div>
  )
}
