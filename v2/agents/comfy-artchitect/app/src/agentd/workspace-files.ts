/* The files THIS chat has in the workspace — read from the folders the chat owns.
 *
 * WHY A DIRECTORY READ AND NOT THE DECLARATIONS. The file panel used to be built from what tools
 * DECLARED — the artifacts a comfy_emit or comfy_download named in its result. That is five hops
 * (sandbox -> engine -> resolver -> event -> this window) that all have to agree before a file
 * sitting on disk is visible, and one of them disagreed for a whole afternoon: every emitted
 * workflow existed and the panel said nothing. A folder cannot disagree. If the file is there it
 * is listed — whichever model wrote it, whatever the tool said about it, whichever daemon version
 * ran. The declarations still do what they are good at: inline previews in the thread, the moment
 * a tool makes something.
 *
 * WHY PER-CHAT FOLDERS. The workspace is the ACCOUNT's, shared by every conversation, so a flat
 * workflows/ or outputs/ held every chat's files at once — seven old workflows beside this job's
 * two — and two jobs that named a workflow the same overwrote each other's. references/ solved
 * this first, one folder per chat named by the session key; the other two now follow it.
 *
 * THE FOLDER NAME IS A CONTRACT with plugins/comfy-bridge/chat_paths.py, which writes where this
 * reads. The two must agree or the window looks where the plugin never writes. Chat keys are
 * already path-safe ("chat-<time>-<rand>"); e2e and peer keys carry colons, which fold to
 * underscores on both sides.
 *
 * WHICH COPY. A sandboxed plugin sees the workspace at its own prefix (/tmp/exec-<id>/ws on a
 * microVM) and its writes sync back to the daemon's copy; this lists the daemon's copy, which is
 * also the one `/file` serves, so every path handed out here can be opened. It survives a dead GPU
 * and a reload for the same reason the folders do: they are on the daemon's disk, not on the
 * instance and not in this window's memory.
 */

import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact, ArtifactKind } from './artifacts'
import { AGENT_ID } from './client'

/** The three kinds of folder a chat owns, each `<kind>/<chat>/` under the workspace. */
export const CHAT_DIRS = ['references', 'workflows', 'outputs'] as const
export type ChatDirKind = (typeof CHAT_DIRS)[number]

/** One chat's folder name. THE SAME RULE AS chat_paths.chat_folder — keep them identical. */
export const chatFolder = (sessionKey: string): string =>
  sessionKey.replace(/[^A-Za-z0-9._-]/g, '_') || '_'

/** `workflows/<chat>` — the workspace-relative path the daemon's workspace.* ops take. */
export const chatDirFor = (kind: ChatDirKind, sessionKey: string): string =>
  `${kind}/${chatFolder(sessionKey)}`

const MEDIA: ArtifactKind[] = ['image', 'video', 'audio']

/** One folder's files as artifacts. "not a directory" is the daemon's word for "nothing there
 *  yet" — an empty list, not an error to show. */
async function listFolder(client: AgentdClient, path: string): Promise<Artifact[]> {
  const res = (await client.request('workspace.list', { agentId: AGENT_ID, path })) as {
    entries?: Array<Record<string, unknown>>
    error?: string
  }
  const entries = Array.isArray(res?.entries) ? res.entries : []
  return entries
    .filter((e) => e && e.kind !== 'folder' && e.path)
    .map((e) => ({
      path: String(e.path),
      name: String(e.name || ''),
      mime: '',
      kind: (MEDIA.includes(e.kind as ArtifactKind) ? e.kind : 'file') as ArtifactKind,
      size: Number(e.size || 0),
      modified: Number(e.modified || 0) || undefined,
    }))
}

/** `version` is the store's `workspaceVersion`: bumped by an upload, by a tool result that
 *  declared a file, and by the end of a turn — so the list re-reads the moment something lands
 *  rather than on the next chat switch. */
export function useChatWorkspaceFiles(
  client: AgentdClient | undefined,
  sessionKey: string,
  version: number,
): Artifact[] {
  const [files, setFiles] = useState<Artifact[]>([])

  useEffect(() => {
    if (!client || !sessionKey) {
      setFiles([])
      return
    }
    let stale = false
    void (async () => {
      const lists = await Promise.all(
        CHAT_DIRS.map((kind) =>
          listFolder(client, chatDirFor(kind, sessionKey)).catch((): Artifact[] => []),
        ),
      )
      if (!stale) setFiles(lists.flat())
    })()
    return () => {
      stale = true
    }
  }, [client, sessionKey, version])

  return files
}

/** The panel's list: what the thread DECLARED first — those carry a mime and arrive the instant a
 *  tool names them — then whatever the folders hold that the thread did not mention. The same
 *  file from both sources is one entry, keyed case-insensitively with one separator: a desktop
 *  daemon hands back Windows paths, and the two sources need not spell them identically. */
export function mergeFiles(declared: Artifact[], listed: Artifact[], sessionKey: string): Artifact[] {
  const key = (p: string) => p.replace(/\\/g, '/').toLowerCase()
  const present = new Set(listed.map((a) => key(a.path)))
  /* THE FOLDER IS THE TRUTH FOR EXISTENCE. A declaration lives in the transcript for the life
     of the conversation and comes back on every reload; the file it names does not have to.
     Delete a render — through the agent, another window, anything — and the declared row used
     to stay: clickable, draggable, downloadable, every one of them a 404, and the stale-selection
     guard never fired because the path never left this list. So a declared entry that lives in
     one of THIS chat's three folders is shown only while the folder lists it. Declared paths
     elsewhere are left alone — this window has no listing to check them against. */
  const ours = CHAT_DIRS.map((kind) => `/${key(chatDirFor(kind, sessionKey))}/`)
  const inChatFolder = (p: string) => {
    const k = `/${key(p)}`
    return ours.some((dir) => k.includes(dir))
  }
  const kept = declared.filter((a) => !inChatFolder(a.path) || present.has(key(a.path)))
  const seen = new Set(kept.map((a) => key(a.path)))
  return [...kept, ...listed.filter((a) => !seen.has(key(a.path)))]
}
