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
  /** authored | installed | curated | web-app. Absent on an older daemon. */
  origin?: string
  /** 'account' | 'org' | 'shared' — which layer the caller's copy came from. The My-agents
   *  section keys off this, because `mine` is presumed true for the whole shared catalogue. */
  layer?: string
  /** whose it is (tenancy E5): 'org' rows are the organization's, spanning every member;
   *  'personal'/absent is an individual's. Drives the "external" tag for a team. */
  scope?: 'personal' | 'org'
  /** the owning organization when scope === 'org'. */
  orgId?: string
  /** the ACCOUNT ID of who authored this copy — set on an org share, where `owner` is the org and
   *  would otherwise erase the maker. '' / absent on a personal row (there `mine` says whose). */
  author?: string
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
  app?: { title: string; url: string; mode?: string; standalone?: boolean }
}

/** Agents this window can open. Agent Builder itself is excluded: it is the thing doing the
 *  building, and offering "work on Agent Builder" in a picker meant for its output is a trap.
 *
 *  SO IS EVERY OTHER PRODUCT SURFACE. An agent that declares `[app] standalone = true` is a
 *  feature of agentd rather than one of the user's agents, and the same trap applies to all of
 *  them: on a desktop this window can see Cloud Agent Builder, which is no more a thing you
 *  "work on" than this one is. Read from the agent's own declaration, never from its id, so a
 *  fork or a rename cannot slip back into the list. */
export const openable = (agents: AgentRow[]): AgentRow[] =>
  agents.filter((a) => a.id !== AGENT_ID && !a.app?.standalone)

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

/* THE UNIFIED-SHELF LABELS (tenancy E5) — one list of every agent, differentiated on the row by
   two facts the daemon already sends: who authored it, and whether it came from outside the
   caller's world. Pure, so every surface renders them the same way. */

/** A short, human-ish account id when no email is known — "labelled by their user id" without the
 *  full opaque string. */
export const shortId = (id: string): string => {
  const s = String(id || '')
  return s.length > 10 ? `${s.slice(0, 9)}…` : s
}

/** The byline — who made this copy, resolved to an email via `emails`, else a short id, else
 *  'you' for the caller's own. '' when the row's own state already says whose (a personal agent
 *  with no stamped author that is not the caller's). */
export const agentAuthorLabel = (
  a: AgentRow,
  myId: string,
  emails: Record<string, string>,
): string => {
  const author = a.author || ''
  if (author) return author === myId ? 'you' : emails[author] || shortId(author)
  if (a.mine !== false && a.scope !== 'org') return 'you'
  return ''
}

/** Is this copy the caller's OWN work? On an ORG share `owner` is the org, so `author` is the only
 *  field still naming the maker; on a personal row there is no author and origin/mine answer it.
 *  An absent `origin` (an older daemon) reads as authored — when we cannot tell, never brand
 *  somebody's own agent as a foreign import. */
const isOwnWork = (a: AgentRow, myId: string): boolean =>
  a.author ? a.author === myId : (a.origin || 'authored') === 'authored' && a.mine !== false

/** Outside the caller's world — ONE rule carrying both boundaries. Your own work is never external
 *  (a personal draft you are still building is yours, not something that arrived from outside), and
 *  for a team its organization's agents are not external either. Everything else is: an installed or
 *  curated copy, or somebody else's agent.
 *
 *  The earlier form said "not an org agent" for a team, which tagged an enterprise user's OWN
 *  unshared draft as external — literally true and obviously wrong on screen. */
export const agentIsExternal = (a: AgentRow, enterprise: boolean, myId = ''): boolean =>
  !isOwnWork(a, myId) && !(enterprise && a.scope === 'org')
