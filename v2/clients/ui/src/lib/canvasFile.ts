/**
 * Canvas viewer classification — a finer-grained kind than the coarse artifact kind, so
 * the Canvas can pick the right viewer per file type. Extensible: add an extension to a
 * set (or a new kind + viewer) as we grow the canvas.
 */

import type { Artifact } from './artifacts'

export type ViewerKind =
  | 'image'
  | 'pdf'
  | 'video'
  | 'audio'
  | 'markdown'
  | 'html'
  | 'csv'
  | 'code'
  | 'office'
  | 'unknown'

const IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif', 'tif', 'tiff', 'svg'])
const VIDEO = new Set(['mp4', 'webm', 'mov', 'mkv', 'm4v', 'ogv'])
const AUDIO = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'])
const OFFICE = new Set(['pptx', 'ppt', 'docx', 'doc', 'xlsx', 'xls', 'odt', 'odp', 'ods'])
const CODE = new Set([
  'py', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'json', 'jsonc', 'yaml', 'yml', 'toml',
  'xml', 'txt', 'text', 'log', 'css', 'scss', 'less', 'sh', 'bash', 'zsh', 'rb', 'go',
  'rs', 'java', 'kt', 'c', 'h', 'cpp', 'cc', 'hpp', 'cs', 'php', 'sql', 'ini', 'cfg',
  'conf', 'env', 'r', 'lua', 'pl', 'swift', 'dart', 'vue', 'svelte', 'gradle', 'puml',
])

export function ext(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

export function viewerKind(a: Artifact): ViewerKind {
  const e = ext(a.name)
  if (e === 'pdf') return 'pdf'
  if (e === 'md' || e === 'markdown') return 'markdown'
  if (e === 'html' || e === 'htm') return 'html'
  if (e === 'csv' || e === 'tsv') return 'csv'
  if (IMAGE.has(e)) return 'image'
  if (VIDEO.has(e)) return 'video'
  if (AUDIO.has(e)) return 'audio'
  if (OFFICE.has(e)) return 'office'
  if (CODE.has(e)) return 'code'
  // no telling extension (e.g. a synthetic in-memory doc named for display) — consult the mime
  if (a.mime === 'text/markdown') return 'markdown'
  if (a.mime === 'text/html') return 'html'
  if (a.mime === 'text/csv') return 'csv'
  // fall back to the artifact's coarse kind for anything unlisted
  if (a.kind === 'image') return 'image'
  if (a.kind === 'video') return 'video'
  if (a.kind === 'audio') return 'audio'
  return 'unknown'
}

/** Kinds we can edit + save back (text-based). */
export const EDITABLE: ReadonlySet<ViewerKind> = new Set(['code', 'markdown', 'html', 'csv'])

/** A short human label for the header, e.g. "PNG image", "Python", "Markdown". */
export function kindLabel(a: Artifact): string {
  const e = ext(a.name).toUpperCase()
  switch (viewerKind(a)) {
    case 'image': return `${e || 'Image'} image`
    case 'pdf': return 'PDF'
    case 'video': return `${e || 'Video'} video`
    case 'audio': return `${e || 'Audio'} audio`
    case 'markdown': return 'Markdown'
    case 'html': return 'HTML'
    case 'csv': return e === 'TSV' ? 'TSV' : 'CSV'
    case 'office': return `${e} document`
    case 'code': return e || 'Text'
    default: return e || 'File'
  }
}
