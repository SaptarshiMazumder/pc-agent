/* Ad Studio's window: a rail, then the chat beside the studio.
 *
 *   rail  | chat (thread + composer)  ┆  studio (the campaign's gate, its stills and clips, the cast)
 *
 * THE STUDIO SHOWS, THE CHAT ACTS. Everything in the studio is read through the agent's read-only
 * tools; every decision there is sent as the user's own chat message, so the daemon's approval
 * stamps — not a button — are what open a gate. (agentd/campaigns.ts, studio/GatePanel.tsx)
 *
 * The shared screens (credits, organizations, settings, sign-in) are the platform's, under
 * src/common/, and are not edited here.
 */

import { useEffect } from 'react'
import { Clapperboard, Images, Shirt, Sparkles, Users } from 'lucide-react'

import { campaignStatus } from './agentd/campaigns'
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
import { ChatResizer } from './studio/ChatResizer'
import { Studio } from './studio/Studio'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'
import { Settings } from './common/settings/Settings'

import './studio/studio.css'

const AGENT_NAME = 'Ad Studio'

/* The four ways in. Each SEEDS the composer: the product photos still have to be attached. */
const OPENINGS: { icon: JSX.Element; title: string; sub: string; prompt: string }[] = [
  {
    icon: <Clapperboard size={15} strokeWidth={1.8} />,
    title: 'Make an ad',
    sub: 'Attach the product photos',
    prompt: 'Make an ad for this product. ',
  },
  {
    icon: <Users size={15} strokeWidth={1.8} />,
    title: 'With one of our models',
    sub: 'Pick a cast member',
    prompt: 'Make an ad for this product with one of our existing models. ',
  },
  {
    icon: <Shirt size={15} strokeWidth={1.8} />,
    title: 'Direct the look',
    sub: 'Outfit, place, light, mood',
    prompt: 'Make an ad for this product. She wears … ; location … ; time of day … ; mood … ',
  },
  {
    icon: <Sparkles size={15} strokeWidth={1.8} />,
    title: 'New cast member',
    sub: 'A recurring AI model',
    prompt: 'Create a new cast member named : the attached image is AI-generated, not a real person — keep her face exactly. ',
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
  const chatWidth = useApp((s) => s.chatWidth)
  const session = useSession()

  const { send, abort, addFiles, removeFile } = useRun(client)
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
        account={account}
        client={client ?? undefined}
        status={status}
        name={AGENT_NAME}
        counts={{ credits: credits === null ? undefined : credits.toLocaleString() }}
        groupLabel="Studio"
        sharedGroupLabel="Account"
        extraDestinations={[
          { id: 'campaigns', label: 'Campaigns', icon: <Images size={15} /> },
          { id: 'cast', label: 'Cast', icon: <Users size={15} /> },
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
        ) : view === 'cast' ? (
          <CastPage
            client={client}
            onUse={(prompt) => {
              newSession()
              seedComposer(prompt)
            }}
          />
        ) : (
          <div className="st-cols">
            <section className="st-convo" style={{ width: chatWidth }}>
              <header className="convo-head">
                <h1 className="convo-title">{empty ? 'New ad' : openChat?.title || 'Ad'}</h1>
                {pct !== null && (
                  <span className="meter" title={`${session.usage!.used} of ${session.usage!.limit} tokens`}>
                    {pct}% context
                  </span>
                )}
              </header>

              {empty ? (
                <div className="opening">
                  <span className="opening-eyebrow">Ad Studio</span>
                  <h2 className="opening-headline">What are we selling today?</h2>
                  <p className="opening-blurb">
                    Attach the product photos and say who wears it and where. Every step stops for your say
                    before anything more is spent.
                  </p>
                  <div className="opening-grid">
                    {OPENINGS.map((o) => (
                      <button key={o.title} className="opening-card" onClick={() => seedComposer(o.prompt)}>
                        <span className="opening-card-ico">{o.icon}</span>
                        <span className="opening-card-text">
                          <span className="opening-card-title">{o.title}</span>
                          <span className="opening-card-sub">{o.sub}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <Thread items={session.items} running={session.running} onSuggest={(p) => void send(p)} />
              )}

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
                placeholder="Attach product photos, say who and where…"
                meter={null}
              />
            </section>

            <ChatResizer />

            <Studio client={client} session={currentKey} running={session.running} onAnswer={(t) => void send(t)} />
          </div>
        )}
      </main>
    </div>
  )
}
