/* The agent roster — every agent on this machine, and which one this conversation is about.
 *
 * Reading other agents is a privilege the daemon grants Agent Builder alone (CROSS_AGENT_READS).
 * It covers READS only: this window can list, inspect and open any agent's files, and can never
 * chat or write as one.
 */

import type { AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AGENT_ID, useDaemonEvent } from './client'

export interface AgentRow {
  id: string
  name?: string
  tagline?: string
  description?: string
  version?: string
  color?: string
  /** Is this the caller's own agent? Absent on an older daemon — see `publishable`. */
  mine?: boolean
  /** authored | installed | curated. Absent on an older daemon. */
  origin?: string
  /**
   * This agent's own window, when it has one that WORKS.
   *
   * The daemon only fills this in for an agent that declares `[app]` AND whose entry file is
   * actually on disk (gateway `_agent_app`), so its presence is the whole test for "can this be
   * opened" — a half-built window advertises nothing rather than offering a button that 404s.
   */
  app?: { title: string; url: string; mode?: string }
}

/** Agents this window can open. Agent Builder itself is excluded: it is the thing doing the
 *  building, and offering "work on Agent Builder" in a picker meant for its output is a trap. */
export const openable = (agents: AgentRow[]): AgentRow[] =>
  agents.filter((a) => a.id !== AGENT_ID)

/* Publish is OWNERSHIP-gated. The daemon marks each agents.list row with `mine` (is it the
   caller's) and `origin` (authored | installed | curated). A catalogue agent stays fully
   openable, but publishing it would upload someone else's work under this user's creator
   identity; an INSTALLED agent is theirs to use but its author is the only one who can ship a new
   version. The tool refuses both server-side — the button just says so up front instead of
   letting a click discover it. Checks are exact (`=== false`, exact strings), never falsy: an
   older daemon sends neither field, and greying every Publish on that absence would turn a
   missing feature into a broken one. */
export const publishable = (a: AgentRow | null): boolean =>
  !!a && a.mine !== false && a.origin !== 'installed' && a.origin !== 'curated'

export const publishBlockReason = (a: AgentRow | null): string =>
  a && a.mine === false
    ? 'Part of this deployment, not your agent — agents you create here are publishable.'
    : 'Installed from the marketplace — only its author can publish a new version.'

const COLORS = ['#8b74ff', '#5ec8c0', '#f0a45d', '#e8749b', '#7bb4f2', '#b88bd8']
export const agentColor = (a: AgentRow, i: number): string =>
  a.color || COLORS[i % COLORS.length] || '#8b74ff'

/**
 * The roster, kept current with no polling — `agents.changed` is broadcast when an agent is
 * created, reloaded or installed.
 *
 * `onBorn` fires when exactly one agent appears that was not there before. That agent was just
 * BUILT, in this window, by this conversation — focusing it is what the inspector is for, and
 * making the user go and pick it would be asking them to find what they just asked for.
 */
export function useAgents(client: AgentdClient, ready: boolean, onBorn: (agent: AgentRow) => void) {
  const [agents, setAgents] = useState<AgentRow[]>([])
  // null until the first load, so a cold start is not "everything is new"
  const seen = useRef<Set<string> | null>(null)
  const born = useRef(onBorn)
  born.current = onBorn

  const reload = useCallback(async () => {
    let rows: AgentRow[] = []
    try {
      const res = await client.agents()
      rows = (res?.agents as AgentRow[]) || []
    } catch {
      // The roster is chrome. A failure here leaves the list empty and the rest of the window
      // working; the connection status in the rail is what reports a daemon that is not there.
      rows = []
    }
    const ids = new Set(openable(rows).map((a) => a.id))
    if (seen.current) {
      const fresh = [...ids].filter((id) => !seen.current!.has(id))
      if (fresh.length === 1) {
        const agent = rows.find((a) => a.id === fresh[0])
        if (agent) born.current(agent)
      }
    }
    seen.current = ids
    setAgents(rows)
  }, [client])

  useDaemonEvent(client, 'agents.changed', () => void reload())

  // Gated on the socket being OPEN. A request fired before the connection is up fails, and this
  // hook has no retry — the roster would sit empty until something else happened to change it.
  useEffect(() => {
    if (ready) void reload()
  }, [ready, reload])

  return { agents, reload }
}
