/* The story's collection — what its post is made from: the results picked in its campaigns, plus
 * anything added here (uploaded, or from the library). Reorder and remove freely; "Make the post"
 * asks the agent for the story's post. Free and instant (the collection tools). */

import type { AgentdClient } from '@agentd/client'
import { ArrowDown, ArrowUp, Library, Loader2, Megaphone, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { isVideo, type Media } from '../agentd/campaigns'
import { libraryToCollection } from '../agentd/library'
import { importToCollection, listCollections, updateCollection, type Collection } from '../agentd/posts'
import { updateStory } from '../agentd/stories'
import { useApp } from '../state/store'
import { ExpandMedia } from './ExpandMedia'
import { LibraryPicker } from './LibraryPicker'
import { viewerItem } from './ViewableMedia'

export function StoryCollectionTray({
  client,
  story,
  busy,
  onSend,
}: {
  client: AgentdClient | null
  story: { id: string; title: string; collection: string }
  busy: boolean
  onSend: (text: string) => void
}) {
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const [data, setData] = useState<{ collection: Collection | null; media: Media } | null>(null)
  const [error, setError] = useState('')
  const [working, setWorking] = useState(false)
  const [picking, setPicking] = useState(false)
  const pick = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!client) return
    listCollections(client)
      .then((d) => setData({ collection: d.collections.find((c) => c.slug === story.collection) || null, media: d.media }))
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick, story.collection])

  const items = data?.collection?.items || []
  const run = async (what: () => Promise<void>) => {
    setWorking(true)
    setError('')
    try {
      await what()
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setWorking(false)
    }
  }
  // The first file added makes the collection (named after the story) and ties it to the story.
  const target = () => ({ name: story.collection || story.title, isNew: !story.collection })
  const tie = async (made: Collection) => {
    if (!story.collection) await updateStory(client!, story.id, { collection: made.slug })
  }
  const move = (i: number, by: -1 | 1) => {
    const paths = items.map((x) => x.path)
    const j = i + by
    if (j < 0 || j >= paths.length) return
    ;[paths[i], paths[j]] = [paths[j], paths[i]]
    void run(() => updateCollection(client!, story.collection, 'reorder', { paths }))
  }

  return (
    <section className="story-tray">
      <header className="story-tray-head">
        <span className="strip-label">Collection — what the post is made from</span>
        <div className="story-tray-tools">
          <button className="ref-add" disabled={!client || working} onClick={() => pick.current?.click()}>
            {working ? <Loader2 size={11} className="spin" /> : <Upload size={11} />} Upload
          </button>
          <button className="ref-add" disabled={!client || working} onClick={() => setPicking(true)}>
            <Library size={11} /> From library
          </button>
          <button className="prime-btn" disabled={busy || !items.length} onClick={() => onSend(`Make the post for the story ${story.id}.`)}>
            <Megaphone size={13} /> Make the post
          </button>
        </div>
      </header>
      <input
        ref={pick}
        type="file"
        accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files || [])
          e.target.value = ''
          if (files.length && client)
            void run(async () => {
              const t = target()
              await tie(await importToCollection(client, t.name, t.isNew, files, story.title))
            })
        }}
      />
      {error && <div className="studio-error">{error}</div>}
      {items.length === 0 ? (
        <p className="page-note">Nothing in it yet — pick results in the stages, or add your own here.</p>
      ) : (
        <div className="story-tray-items">
          {items.map((it, i) => (
            <div key={it.path} className="story-tray-item has-expand">
              {isVideo(it.path) ? <video src={data!.media(it.path)} muted preload="metadata" /> : <img src={data!.media(it.path)} alt="" />}
              <ExpandMedia src={data!.media(it.path)} path={it.path} set={items.map((x) => viewerItem(data!.media(x.path), x.path, x.product || ''))} />
              <span className="story-tray-n">{i + 1}</span>
              <span className="story-tray-acts">
                <button className="ref-x" disabled={i === 0 || working} onClick={() => move(i, -1)} title="Earlier">
                  <ArrowUp size={10} />
                </button>
                <button className="ref-x" disabled={i === items.length - 1 || working} onClick={() => move(i, 1)} title="Later">
                  <ArrowDown size={10} />
                </button>
                <button className="ref-x" disabled={working} onClick={() => void run(() => updateCollection(client!, story.collection, 'remove', { paths: [it.path] }))} title="Take it out">
                  <Trash2 size={10} />
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
      {picking && (
        <LibraryPicker
          client={client}
          kind="any"
          title="Add to the story's collection"
          onPick={(paths) => {
            setPicking(false)
            void run(async () => {
              const t = target()
              await tie(await libraryToCollection(client!, t.name, t.isNew, paths))
            })
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </section>
  )
}
