/* The file tree — what a build is PRODUCING, as it happens.
 *
 * ONE root, on purpose: `definition`, i.e. `agents/<id>/` — agent.toml, IDENTITY.md, skills/,
 * plugins/, ui/. That is what building an agent writes, and watching those files appear (and
 * flash) is this panel's entire job.
 *
 * The daemon's other root, `workspace`, is what an agent produces when it RUNS. It used to be a
 * second tab here and it did not belong: nothing in the build writes there, so it was a tab you
 * would only ever open by mistake.
 *
 * It can point at ANY agent because the daemon lists agent-builder in CROSS_AGENT_READS — a
 * privilege no other agent app has, and one that covers reads only.
 */

import type { AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useRef, useState } from 'react'

const ROOT = 'definition'

export interface TreeEntry {
  name: string
  path: string
  rel: string
  kind: string
  size: number
}

export interface TreeRow extends TreeEntry {
  depth: number
  expanded: boolean
  /** Written since the last refresh — flashed, so you SEE the build happening. */
  fresh: boolean
}

export const GLYPH: Record<string, string> = {
  folder: '▸',
  image: '◧',
  video: '▶',
  audio: '♪',
  file: '·',
}

export function formatSize(n: number): string {
  if (!n) return ''
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${v < 10 && i ? v.toFixed(1) : Math.round(v)}${u[i]}`
}

export function useAgentFiles(client: AgentdClient, agentId: string | null) {
  const [rows, setRows] = useState<TreeRow[]>([])
  const [error, setError] = useState('')
  const expanded = useRef(new Set<string>())
  const known = useRef(new Set<string>())
  // Which agent the rows on screen belong to. A slow listing for the previous agent must not land
  // in the panel after the user has moved on.
  const showing = useRef<string | null>(null)

  const list = useCallback(
    async (path: string): Promise<{ entries: TreeEntry[]; error: string }> => {
      try {
        const res: any = await client.request('workspace.list', { agentId, path, root: ROOT })
        if (res?.error) return { entries: [], error: String(res.error) }
        return { entries: (res?.entries as TreeEntry[]) || [], error: '' }
      } catch (e) {
        return { entries: [], error: String((e as Error)?.message || e) }
      }
    },
    [client, agentId],
  )

  const refresh = useCallback(async () => {
    if (!agentId) {
      setRows([])
      setError('')
      return
    }
    const forAgent = agentId
    const out: TreeRow[] = []
    const seen = new Set<string>()
    let failure = ''

    /** Depth-first over the expanded set. Sequential on purpose: a handful of small directory
     *  reads, and ordering matters more than shaving a few ms. */
    const walk = async (path: string, depth: number): Promise<void> => {
      const { entries, error: err } = await list(path)
      if (err) {
        // Only the top-level failure is worth surfacing — a sub-directory that vanished mid-walk
        // is noise next to "this agent has no files".
        if (!depth) failure = err
        return
      }
      for (const e of entries) {
        const isDir = e.kind === 'folder'
        const isOpen = expanded.current.has(e.rel)
        if (!isDir) seen.add(e.path)
        out.push({
          ...e,
          depth,
          expanded: isOpen,
          fresh: !isDir && known.current.size > 0 && !known.current.has(e.path),
        })
        if (isDir && isOpen) await walk(e.rel, depth + 1)
      }
    }

    await walk('', 0)
    if (showing.current !== forAgent && showing.current !== null) return
    known.current = seen
    setRows(out)
    setError(failure)
  }, [agentId, list])

  const toggle = useCallback(
    (rel: string) => {
      const set = expanded.current
      if (set.has(rel)) set.delete(rel)
      else set.add(rel)
      void refresh()
    },
    [refresh],
  )

  // A different agent is a different tree: its expanded folders and its "what is new" baseline
  // both belong to it, and carrying either across would flash every file in the new one.
  useEffect(() => {
    expanded.current = new Set()
    known.current = new Set()
    showing.current = agentId
    void refresh()
  }, [agentId, refresh])

  return { rows, error, refresh, toggle }
}
