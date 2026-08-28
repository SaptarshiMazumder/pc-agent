/* The agent roster — every agent on this machine, and which one this conversation is about.
 *
 * THE ROSTER ITSELF LIVES IN THE STORE (state/store.ts), with the `agents.changed` subscription
 * and the newborn-focus rule that used to sit in `useAgents` here. What stays is the row shape
 * and the pure predicates over it — who may be opened, who may be published, what colour a row
 * gets — none of which need a connection.
 *
 * Reading other agents is a privilege the daemon grants Agent Builder alone (CROSS_AGENT_READS).
 * It covers READS only: this window can list, inspect and open any agent's files, and can never
 * chat or write as one.
 */

import { AGENT_ID } from './client'

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
  /** 'account' | 'org' | 'shared' — which layer the caller's copy came from. The My-agents
   *  section keys off this, because `mine` is presumed true for the whole shared catalogue. */
  layer?: string
  /**
   * WHERE THIS AGENT'S DEFINITION IS, absolutely.
   *
   * NOT from `agents.list` — that surface is app-scoped, so putting a filesystem path in it would
   * hand every agent's window the server's paths on a hosted daemon. It comes from
   * `create_agent`'s own result, which goes only to whoever made the call.
   *
   * So it is known for an agent created in this window and absent for one merely opened from the
   * sidebar. The preamble treats it that way.
   */
  dir?: string
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
