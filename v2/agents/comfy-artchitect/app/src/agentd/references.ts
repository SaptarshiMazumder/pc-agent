/* The reference media THIS chat added, as files — read from the workspace folder the button
 * writes to (`references/<chat>/`, see run.ts).
 *
 * WHY A SEPARATE READ. The file panel is built from what tools DECLARE — the workflows, renders
 * and downloads the agent produces. A reference is an INPUT: the button uploads it straight to
 * the workspace and the agent only ever hears its filename, so nothing ever declared it and the
 * panel could not show what the agent had been given. This reads the folder itself. It survives
 * a dead GPU and a reload for the same reason the folder does: it is on the daemon's disk, not
 * on the instance and not in this window's memory.
 */

import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact, ArtifactKind } from './artifacts'
import { AGENT_ID } from './client'
import { referenceDirFor } from './run'

const MEDIA: ArtifactKind[] = ['image', 'video', 'audio']

/** `version` is the store's `referencesVersion`: bumped by the button after an upload, so the
 *  list re-reads the moment a file lands rather than on the next chat switch. */
export function useChatReferences(
  client: AgentdClient | undefined,
  sessionKey: string,
  version: number,
): Artifact[] {
  const [refs, setRefs] = useState<Artifact[]>([])

  useEffect(() => {
    if (!client || !sessionKey) {
      setRefs([])
      return
    }
    let stale = false
    void (async () => {
      try {
        const res = (await client.request('workspace.list', {
          agentId: AGENT_ID,
          path: referenceDirFor(sessionKey),
        })) as { entries?: Array<Record<string, unknown>>; error?: string }
        if (stale) return
        // "not a directory" is the daemon's word for "nothing added yet" — an empty list, not
        // an error to show.
        const entries = Array.isArray(res?.entries) ? res.entries : []
        setRefs(
          entries
            .filter((e) => e && e.kind !== 'folder' && e.path)
            .map((e) => ({
              path: String(e.path),
              name: String(e.name || ''),
              mime: '',
              kind: (MEDIA.includes(e.kind as ArtifactKind) ? e.kind : 'file') as ArtifactKind,
              size: Number(e.size || 0),
            })),
        )
      } catch {
        if (!stale) setRefs([])
      }
    })()
    return () => {
      stale = true
    }
  }, [client, sessionKey, version])

  return refs
}
