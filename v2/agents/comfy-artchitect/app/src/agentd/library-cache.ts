/* The Library's last known contents, kept for this browser tab.
 *
 * WHY. Opening the Library meant a blank panel until two daemon round trips came back, every
 * time, in both places it is shown. The cache lets the panel draw what it had at once and then
 * swap in the fresh list — the daemon stays the truth, this is only the first frame.
 *
 * SESSION STORAGE, not local: it dies with the tab, so a list cannot outlive the sign-in that
 * read it by more than one visit. Every access is wrapped — a private window or blocked site
 * data throws, and that must mean "no cache", never a broken panel.
 */

import type { Artifact } from './artifacts'
import type { LibraryItem } from './library'

const KEY = 'comfy-library-v1'

export type LibrarySnapshot = { items: LibraryItem[]; media: Map<string, Artifact> }

export function readLibraryCache(): LibrarySnapshot | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { items?: LibraryItem[]; media?: [string, Artifact][] }
    if (!Array.isArray(parsed.items) || !Array.isArray(parsed.media)) return null
    return { items: parsed.items, media: new Map(parsed.media) }
  } catch {
    return null
  }
}

export function writeLibraryCache(snap: LibrarySnapshot): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ items: snap.items, media: [...snap.media.entries()] }))
  } catch {
    // Storage full or blocked: the next open simply fetches, as it did before the cache.
  }
}
