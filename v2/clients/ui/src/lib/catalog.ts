/**
 * Reading a registry's catalog.json — the marketplace WITHOUT a daemon.
 *
 * The daemon reaches the same rows over `marketplace.catalog` (it builds them from the index it
 * verified). This module is the other door: a plain fetch of the static file the publish service
 * writes beside index.json, for the public marketplace page, which has no socket, no session and
 * no keys.
 *
 * IT VERIFIES NOTHING, deliberately. The page renders links; it never downloads or executes a
 * bundle. Signature checking belongs where execution is — the daemon on install, the installer
 * stub on download — and a browser checking a signature it then ignores is theatre. Nothing here
 * may ever become the input to an install.
 */

import type { CatalogBundle } from '../gateway/protocol'

/** The generated document. Mirrors agent_runtime/domain/catalog.py — change both together. */
export interface CatalogDoc {
  schema: number
  registry: string
  publisher: string
  /** what relative artifact urls resolve against ('' => the catalog's own location) */
  base: string
  /** where web-delivered agents run ('' => no Open-in-browser links anywhere) */
  webHost: string
  bundles: CatalogBundle[]
}

/** The shape number this client understands. A newer document is refused rather than guessed at. */
export const SUPPORTED_SCHEMA = 1

const EMPTY: CatalogDoc = { schema: SUPPORTED_SCHEMA, registry: '', publisher: '', base: '', webHost: '', bundles: [] }

/**
 * Join one artifact url against the document's base.
 *
 * Rows are relative when the registry is a directory (it has to keep working after being copied)
 * and absolute when the writer knew the public base. `new URL` handles both: an absolute url
 * ignores the base entirely. The fallback is the catalog's own address, which is the right answer
 * for a registry served out of its own folder.
 */
export function assetUrl(doc: CatalogDoc, url: string, catalogUrl = ''): string {
  if (!url) return ''
  try {
    return new URL(url, doc.base || catalogUrl || location.href).toString()
  } catch {
    return url
  }
}

/**
 * Fetch and validate a catalog.
 *
 * Throws with a message meant for a reader, not a console: this is the ONE request the public
 * page makes, so its failure is the whole page's failure and "Failed to fetch" tells a visitor
 * nothing about whether the store is empty, moved, or broken.
 */
export async function fetchCatalog(url: string): Promise<CatalogDoc> {
  let response: Response
  try {
    response = await fetch(url, { cache: 'no-cache' })
  } catch {
    throw new Error(`Could not reach the marketplace registry at ${url}.`)
  }
  if (!response.ok) {
    // A 404 is the common one and it is not an error the visitor caused: it is a registry that
    // has never been published to.
    if (response.status === 404) return { ...EMPTY }
    throw new Error(`The marketplace registry answered ${response.status} for ${url}.`)
  }
  let doc: CatalogDoc
  try {
    doc = (await response.json()) as CatalogDoc
  } catch {
    throw new Error('The marketplace registry returned something that is not a catalog.')
  }
  if (!Array.isArray(doc?.bundles)) {
    throw new Error('The marketplace registry returned a catalog with no bundles list.')
  }
  if (Number(doc.schema || 1) > SUPPORTED_SCHEMA) {
    throw new Error(
      `This page reads catalog schema ${SUPPORTED_SCHEMA} and the registry published ${doc.schema}. ` +
        'Reload — the page is probably a cached older build.'
    )
  }
  return { ...EMPTY, ...doc }
}
