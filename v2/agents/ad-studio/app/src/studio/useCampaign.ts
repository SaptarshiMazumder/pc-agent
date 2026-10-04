/* Which campaign this chat is on, and its state — re-read whenever the studio's tick moves.
 *
 * A chat's campaign is the newest one `campaign_start` started in it (campaign_list marks them
 * `mine` by the chat's session key). A chat that only CONTINUES a campaign started elsewhere has
 * none of its own; the user can open one from the Campaigns page, which pins it here.
 */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import {
  campaignStatus,
  listCampaigns,
  type CampaignDetail,
  type CampaignRow,
  type Media,
} from '../agentd/campaigns'
import { useApp } from '../state/store'

export interface CampaignView {
  campaign: CampaignDetail | null
  media: Media
  rows: CampaignRow[]
  error: string
  loading: boolean
}

const NO_MEDIA: Media = () => ''

export function useCampaign(client: AgentdClient | null, session: string, pinned: string): CampaignView {
  const tick = useApp((s) => s.studioTick)
  const [view, setView] = useState<CampaignView>({ campaign: null, media: NO_MEDIA, rows: [], error: '', loading: true })

  useEffect(() => {
    if (!client || !session) return
    let gone = false
    void (async () => {
      try {
        const { rows, media } = await listCampaigns(client, session)
        const id = pinned || rows.find((r) => r.mine)?.id || ''
        const detail = id ? await campaignStatus(client, id) : null
        if (!gone) {
          setView({ campaign: detail?.campaign ?? null, media: detail?.media ?? media, rows, error: '', loading: false })
        }
      } catch (e) {
        if (!gone) setView((v) => ({ ...v, error: String((e as Error)?.message || e), loading: false }))
      }
    })()
    return () => {
      gone = true
    }
  }, [client, session, pinned, tick])

  return view
}
