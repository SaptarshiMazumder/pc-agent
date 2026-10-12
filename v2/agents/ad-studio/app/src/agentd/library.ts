/* The library, through the agent's library tools (tools.invoke): every campaign's generations and
 * the user's own uploads in one place, and any mix of them put into a collection. All free and
 * instant, so all called directly. */

import type { AgentdClient } from '@agentd/client'

import type { Media } from './campaigns'
import { readFile } from './chat'
import { AGENT_ID } from './client'
import { invoke, mediaFor, type Collection } from './posts'

export interface LibraryItem {
  path: string
  kind: 'image' | 'video'
  /** A generation of a campaign, or the user's own upload. */
  source: 'campaign' | 'upload'
  campaign: string
  /** The campaign's product; for an upload, the label given (may be ''). */
  product: string
  added: number
}

export interface LibraryCampaign {
  id: string
  name: string
}

export async function listLibrary(client: AgentdClient): Promise<{ items: LibraryItem[]; campaigns: LibraryCampaign[]; media: Media }> {
  const d = await invoke(client, 'library_list', {})
  return { items: (d.items || []) as LibraryItem[], campaigns: (d.campaigns || []) as LibraryCampaign[], media: mediaFor(String(d.root || '')) }
}

/** Upload the user's own images and clips into the library, each under `label` (may be ''). */
export async function uploadToLibrary(client: AgentdClient, files: File[], label: string): Promise<LibraryItem[]> {
  const uploaded = []
  for (const file of files) {
    if (!/\.(png|jpe?g|webp|mp4|mov|webm)$/i.test(file.name)) throw new Error(`${file.name}: use an image (png, jpg, webp) or a clip (mp4, mov, webm)`)
    const read = await readFile(file)
    const res: any = await client.request('workspace.upload', { agentId: AGENT_ID, path: 'uploads', name: file.name, dataBase64: read.dataBase64 })
    if (!res?.ok) throw new Error(`${file.name}: ${String(res?.error || 'upload failed')}`)
    uploaded.push({ path: String(res.path), label })
  }
  return ((await invoke(client, 'library_upload', { files: uploaded })).items || []) as LibraryItem[]
}

export async function removeFromLibrary(client: AgentdClient, path: string): Promise<void> {
  await invoke(client, 'library_remove', { path })
}

/** Library items into a collection: an existing one (its slug), or with `isNew` a new one named `collection`. */
export async function libraryToCollection(client: AgentdClient, collection: string, isNew: boolean, paths: string[]): Promise<Collection> {
  return (await invoke(client, 'library_to_collection', { collection, new: isNew, paths })).collection as Collection
}
