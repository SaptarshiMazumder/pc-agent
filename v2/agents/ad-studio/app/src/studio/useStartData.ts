/* The recipes and the cast an ad can start from — read through the agent's read-only tools, and
 * re-read whenever the studio's tick moves (a new cast member made in the chat shows up). */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import { listCast, listRecipes, type CastMember, type Media, type Recipe } from '../agentd/campaigns'
import { useApp } from '../state/store'

export interface StartData {
  recipes: Recipe[]
  cast: CastMember[]
  media: Media
  error: string
  loaded: boolean
}

const NO_MEDIA: Media = () => ''

export function useStartData(client: AgentdClient | null): StartData {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<StartData>({ recipes: [], cast: [], media: NO_MEDIA, error: '', loaded: false })
  useEffect(() => {
    if (!client) return
    let gone = false
    Promise.all([listRecipes(client), listCast(client)])
      .then(([recipes, cast]) => {
        if (!gone) setData({ recipes, cast: cast.cast, media: cast.media, error: '', loaded: true })
      })
      .catch((e) => {
        if (!gone) setData((d) => ({ ...d, error: String(e?.message || e), loaded: true }))
      })
    return () => {
      gone = true
    }
  }, [client, tick])
  return data
}
