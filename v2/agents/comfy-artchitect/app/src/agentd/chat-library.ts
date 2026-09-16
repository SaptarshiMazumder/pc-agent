/* Everything this account has made, across EVERY chat — the source for the My creations screen.
 *
 * WHY IT IS A FOLDER WALK AND NOT A TRANSCRIPT WALK. Each chat's files already sit in a folder of
 * their own (`workflows/<chat>/`, `outputs/<chat>/` — the contract in workspace-files.ts and
 * plugins/comfy-bridge/chat_paths.py). So "all workflows, by chat" is the `workflows/` folder
 * listed one level down: the folder names ARE the chats, and the files in each are what that chat
 * made. No transcript has to be loaded, no chat has to be open, and a chat that was deleted still
 * shows its files — under "Deleted conversations" — because deleting a chat never deleted its
 * folder.
 *
 * TWO CALLS DEEP, ONE ROUND TRIP WIDE. `workspace.list` lists a single directory, so this lists
 * the kind folder to learn the chats, then every chat folder at once, a handful in flight at a
 * time. Each is a local directory read on the daemon; for the dozens of chats a person has this
 * is well under a second. Hundreds would want a daemon-side tree call instead.
 *
 * TWO PATHS PER FILE. `path` is the daemon's absolute path — the one `/file` serves and the
 * thumbnails and downloads use. `rel` is workspace-relative — the only form `workspace.delete`
 * accepts. They are kept side by side so a delete can never be asked for with the wrong one.
 */

import { useCallback, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact } from './artifacts'
import { AGENT_ID } from './client'
import type { ChatRow } from './sessions'
import { chatFolder, type ChatDirKind } from './workspace-files'

/** One file in a chat's folder, with both of the paths the window needs for it. */
export interface LibraryFile extends Artifact {
  /** Workspace-relative, posix — what `workspace.delete` takes. */
  rel: string
}

/** One chat's folder of a kind: the files it holds and which chat it belongs to. */
export interface ChatGroup {
  /** The folder name — the chat's key, folded to be path-safe. */
  folder: string
  /** The session it belongs to, when that chat still exists. */
  sessionId?: string
  title: string
  /** Newest file's mtime, seconds. What the groups sort by. */
  modified: number
  files: LibraryFile[]
}

const MEDIA = ['image', 'video', 'audio'] as const

interface Entry {
  name?: unknown
  kind?: unknown
  size?: unknown
  modified?: unknown
  rel?: unknown
  path?: unknown
}

async function list(client: AgentdClient, path: string): Promise<Entry[]> {
  const res = (await client.request('workspace.list', { agentId: AGENT_ID, path })) as {
    entries?: Entry[]
  }
  return Array.isArray(res?.entries) ? res.entries : []
}

function toFile(e: Entry): LibraryFile {
  const kind = String(e.kind || '')
  return {
    path: String(e.path || ''),
    rel: String(e.rel || ''),
    name: String(e.name || ''),
    mime: '',
    kind: (MEDIA as readonly string[]).includes(kind) ? (kind as LibraryFile['kind']) : 'file',
    size: Number(e.size || 0),
    modified: Number(e.modified || 0) || undefined,
  }
}

/** A few folders at a time — enough to be quick, few enough not to flood one socket. */
const IN_FLIGHT = 6

/** Every chat folder of one kind, with its files. Folders with nothing in them are dropped: an
 *  empty folder is a chat that started and never emitted, which is not a creation. */
export async function listChatFolders(
  client: AgentdClient,
  kind: ChatDirKind,
): Promise<Array<{ folder: string; files: LibraryFile[] }>> {
  const folders = (await list(client, kind)).filter((e) => e.kind === 'folder' && e.name)
  const out: Array<{ folder: string; files: LibraryFile[] }> = []
  for (let i = 0; i < folders.length; i += IN_FLIGHT) {
    const slice = folders.slice(i, i + IN_FLIGHT)
    const lists = await Promise.all(
      slice.map((f) => list(client, `${kind}/${String(f.name)}`).catch((): Entry[] => [])),
    )
    slice.forEach((f, j) => {
      const files = lists[j].filter((e) => e.kind !== 'folder' && e.path).map(toFile)
      if (files.length) out.push({ folder: String(f.name), files })
    })
  }
  return out
}

/** The name a group is shown under when its chat still exists. Rows carry titles once the
 *  daemon has named them; before that, the chat is known by its key. */
export const ORPHAN_TITLE = 'Deleted conversations'

/** Folders -> groups, newest first, matched to the chats they belong to.
 *
 *  THE MATCH IS THE FOLDER RULE run over the session list — the same fold the plugin applied
 *  when it wrote the folder. No id is stored anywhere; the folder name is the id.
 *
 *  Folders without a chat all land in ONE group at the end, because the person cannot open the
 *  chat anyway and a dozen "Deleted conversation" headers would say nothing a single one does not.
 *  Pure, and exported for tests. */
export function groupByChat(
  folders: Array<{ folder: string; files: LibraryFile[] }>,
  chats: ChatRow[],
): ChatGroup[] {
  const byFolder = new Map<string, ChatRow>()
  for (const row of chats) byFolder.set(chatFolder(row.sessionId), row)

  const newest = (files: LibraryFile[]) => Math.max(0, ...files.map((f) => f.modified || 0))
  const live: ChatGroup[] = []
  const orphaned: LibraryFile[] = []
  for (const { folder, files } of folders) {
    const row = byFolder.get(folder)
    if (!row) {
      orphaned.push(...files)
      continue
    }
    live.push({
      folder,
      sessionId: row.sessionId,
      title: row.title || row.snippet || row.sessionId,
      modified: newest(files),
      files: [...files].sort((a, b) => (b.modified || 0) - (a.modified || 0)),
    })
  }
  live.sort((a, b) => b.modified - a.modified)
  if (orphaned.length) {
    orphaned.sort((a, b) => (b.modified || 0) - (a.modified || 0))
    live.push({ folder: '', title: ORPHAN_TITLE, modified: newest(orphaned), files: orphaned })
  }
  return live
}

/** The library of one kind, live.
 *
 *  Re-read when `version` (the store's workspaceVersion) moves — a tool wrote or a turn ended —
 *  and when the chat list changes, because a rename or delete changes which header a folder
 *  sits under. `reload` is for the screen itself, after a delete it performed. */
export function useChatLibrary(
  client: AgentdClient | undefined,
  kind: ChatDirKind,
  chats: ChatRow[],
  version: number,
): { groups: ChatGroup[]; loading: boolean; reload: () => void } {
  const [folders, setFolders] = useState<Array<{ folder: string; files: LibraryFile[] }>>([])
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    if (!client) return
    let stale = false
    setLoading(true)
    void listChatFolders(client, kind)
      .catch(() => [] as Array<{ folder: string; files: LibraryFile[] }>)
      .then((f) => {
        if (stale) return
        setFolders(f)
        setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [client, kind, version, tick])

  return { groups: groupByChat(folders, chats), loading, reload }
}

/** Remove files, straight through the daemon — the same door the reference Replace flow and
 *  comfy_delete's host half use. "not found" counts as done: the file is not there, which is
 *  what was asked. Anything else is thrown, so the screen can say the file is still there
 *  rather than re-listing it with no explanation. */
export async function deleteLibraryFiles(client: AgentdClient, files: LibraryFile[]): Promise<void> {
  for (const f of files) {
    const res = (await client.request('workspace.delete', { agentId: AGENT_ID, path: f.rel })) as {
      ok?: boolean
      error?: string
    }
    if (!res?.ok && res?.error !== 'not found') {
      throw new Error(res?.error || `could not delete ${f.name}`)
    }
  }
}
