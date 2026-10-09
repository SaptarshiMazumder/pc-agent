/* The post this chat is making, if any — or the one opened into it from the Posts page — re-read
 * whenever the studio's tick moves (the agent planned it, changed it, rendered it), and every few
 * seconds while its slides are being designed, so the panel shows the run moving. */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { listPosts, type DesignProgress, type Post } from '../agentd/posts'
import { useApp } from '../state/store'

const POLL_MS = 3000

export function usePost(
  client: AgentdClient | null,
  session: string,
  pinned: string,
): { post: Post | null; media: Media; progress: DesignProgress | null; error: string } {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ post: Post | null; media: Media; progress: DesignProgress | null }>({ post: null, media: () => '', progress: null })
  const [error, setError] = useState('')
  const [poll, setPoll] = useState(0)
  useEffect(() => {
    if (!client || !session) return
    let gone = false
    listPosts(client, pinned ? { post: pinned } : { session })
      .then((d) => {
        if (!gone) {
          const post = d.posts[0] || null
          setData({ post, media: d.media, progress: (post && d.designing[post.slug]) || null })
          setError('')
        }
      })
      .catch((e) => !gone && setError(String(e?.message || e)))
    return () => {
      gone = true
    }
  }, [client, session, pinned, tick, poll])
  // While a design run is moving, look again every few seconds.
  const active = !!data.progress?.active
  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => setPoll((n) => n + 1), POLL_MS)
    return () => window.clearInterval(t)
  }, [active])
  return { ...data, error }
}
