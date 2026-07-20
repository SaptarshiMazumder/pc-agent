/**
 * Platform adapter — the ONE place the renderer touches host capabilities.
 *
 * On DESKTOP the renderer runs in Electron and `window.agentd` (the preload bridge)
 * provides native supervisor + file operations. On the WEB the same renderer runs in a
 * plain browser with no bridge, so this module supplies browser equivalents: the daemon
 * is reached over the network (no supervisor to start/stop), files are streamed from the
 * daemon's HTTP /file endpoint, and saves/pickers use the browser's download/upload.
 *
 * Every component imports `platform` from here instead of `window.agentd`, so a single
 * codebase serves both the desktop app and the hosted web client. The localhost web build
 * IS the production web client — only the endpoint it dials differs (see resolveDaemonUrl).
 */

import { fileUrl } from './artifacts'

/** The host surface (mirrors the desktop preload bridge, src/preload/index.ts). */
export interface AgentdPlatform {
  flavor: () => Promise<Record<string, unknown>>
  supervisorStatus: () => Promise<{ phase: string; message: string }>
  ensureDaemon: () => Promise<{ url: string; version?: string; pid?: number }>
  restartDaemon: () => Promise<unknown>
  onSupervisorStatus: (cb: (status: unknown) => void) => () => void
  openPath: (p: string) => Promise<string>
  revealPath: (p: string) => Promise<void>
  downloadPath: (
    p: string
  ) => Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>
  readText: (p: string) => Promise<{ ok: boolean; text?: string; error?: string }>
  readBytes: (p: string) => Promise<{ ok: boolean; base64?: string; error?: string }>
  savePath: (
    p: string,
    content: string,
    base64?: boolean
  ) => Promise<{ ok: boolean; error?: string }>
  saveAs: (
    defaultName: string,
    content: string,
    base64?: boolean
  ) => Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>
  pickFiles: () => Promise<Array<{ name: string; size: number; dataBase64: string }>>
  openAppWindow: (url: string, title?: string) => Promise<{ ok: boolean; error?: string }>
}

const bridge = (globalThis as { agentd?: AgentdPlatform }).agentd
export const isDesktop = !!bridge

// --------------------------------------------------------------------------- web helpers

/** An EXPLICIT daemon URL the operator specified: ?daemon=<full ws url>, ?url= + ?token=,
 *  or VITE_AGENTD_URL/TOKEN. Returns '' when nothing explicit was given (so ensureDaemon can
 *  fall back to the dev-token middleware, then to same-origin). */
function explicitDaemonUrl(): string {
  const q = new URLSearchParams(location.search)
  const explicit = q.get('daemon')
  if (explicit) return explicit
  const env = (import.meta as { env?: Record<string, string> }).env || {}
  const baseOverride = q.get('url') || env.VITE_AGENTD_URL
  const token = q.get('token') || env.VITE_AGENTD_TOKEN || ''
  if (!baseOverride && !token) return ''
  try {
    const u = new URL(baseOverride || defaultBase())
    if (token) u.searchParams.set('token', token)
    return u.toString()
  } catch {
    return baseOverride || defaultBase()
  }
}

function isLocalhostPage(): boolean {
  const h = typeof location !== 'undefined' ? location.hostname : ''
  return h === 'localhost' || h === '127.0.0.1'
}

/** DEV convenience: ask the vite dev server (see vite.config.web.ts) for the live daemon's
 *  url+token, read straight from ~/.agentd/gateway.json. Re-fetched on every (re)connect, so
 *  a daemon restart (which rotates the token) reconnects with no manual URL juggling. Absent
 *  in production (static hosting) — the fetch just fails and we fall back. */
async function devDaemonUrl(): Promise<string> {
  try {
    const r = await fetch('/__agentd_dev/gateway', { cache: 'no-store' })
    if (!r.ok) return ''
    const d = (await r.json()) as { url?: string; token?: string }
    if (!d?.token) return ''
    const u = new URL(d.url || defaultBase())
    u.searchParams.set('token', d.token)
    return u.toString()
  } catch {
    return '' // not in dev, or daemon down — caller falls back
  }
}

function defaultBase(): string {
  const loc = typeof location !== 'undefined' ? location : null
  // served from a real host (production) -> dial that same host over wss (token comes from
  // the account later); on localhost dev -> the local daemon.
  if (loc && /^https?:$/.test(loc.protocol) && loc.hostname !== 'localhost' && loc.hostname !== '127.0.0.1') {
    return `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}`
  }
  // 127.0.0.1, not "localhost": the daemon binds IPv4 and browsers may resolve localhost to ::1
  return 'ws://127.0.0.1:8787'
}

function b64FromBytes(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

function bytesFromB64(b64: string): Uint8Array {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

function triggerDownload(href: string, name?: string): void {
  const a = document.createElement('a')
  a.href = href
  if (name) a.download = name
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function downloadContent(content: string, name: string, base64?: boolean): void {
  const blob = base64
    ? new Blob([bytesFromB64(content) as unknown as BlobPart])
    : new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  triggerDownload(url, name)
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

function baseName(p: string): string {
  return p.split(/[\\/]/).pop() || 'download'
}

// --------------------------------------------------------------------------- web platform

const webPlatform: AgentdPlatform = {
  async flavor() {
    // hosted web has no installer flavor; sensible defaults (store on, no bundled agent)
    return {
      productId: 'agentd-web',
      productName: 'agentd',
      defaultAgent: '',
      storeEnabled: true,
      preinstalledBundles: [],
      bundledPackages: [],
      version: 'web'
    }
  },
  async supervisorStatus() {
    return { phase: 'running', message: 'connected' } // the daemon is remote & already up
  },
  async ensureDaemon() {
    // 1) explicit override always wins; 2) on localhost, the dev middleware supplies the live
    // token (no juggling, survives restarts); 3) production = same-origin wss (token from login).
    const explicit = explicitDaemonUrl()
    if (explicit) return { url: explicit, version: 'web', pid: 0 }
    if (isLocalhostPage()) {
      const dev = await devDaemonUrl()
      if (dev) return { url: dev, version: 'web', pid: 0 }
    }
    return { url: defaultBase(), version: 'web', pid: 0 }
  },
  async restartDaemon() {
    return undefined // a browser cannot restart a remote daemon — no-op
  },
  onSupervisorStatus() {
    return () => {} // no supervisor lifecycle on the web
  },
  async openPath(p) {
    window.open(fileUrl(p), '_blank', 'noopener')
    return ''
  },
  async revealPath(p) {
    window.open(fileUrl(p), '_blank', 'noopener') // no OS file manager on the web
  },
  async downloadPath(p) {
    triggerDownload(fileUrl(p), baseName(p))
    return { ok: true }
  },
  async readText(p) {
    try {
      const r = await fetch(fileUrl(p))
      if (!r.ok) return { ok: false, error: `HTTP ${r.status}` }
      return { ok: true, text: await r.text() }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  },
  async readBytes(p) {
    try {
      const r = await fetch(fileUrl(p))
      if (!r.ok) return { ok: false, error: `HTTP ${r.status}` }
      return { ok: true, base64: b64FromBytes(new Uint8Array(await r.arrayBuffer())) }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  },
  async savePath(p, content, base64) {
    // A browser cannot write to an arbitrary server path; the closest web semantic is a
    // download. (Writing edits back to the workspace will later go through a workspace.upload
    // RPC — tracked as a web-mode follow-up.)
    downloadContent(content, baseName(p), base64)
    return { ok: true }
  },
  async saveAs(defaultName, content, base64) {
    downloadContent(content, defaultName, base64)
    return { ok: true, path: defaultName }
  },
  pickFiles() {
    return new Promise((resolve) => {
      const input = document.createElement('input')
      input.type = 'file'
      input.multiple = true
      input.onchange = async () => {
        const files = Array.from(input.files || [])
        const out: Array<{ name: string; size: number; dataBase64: string }> = []
        for (const f of files) {
          const bytes = new Uint8Array(await f.arrayBuffer())
          out.push({ name: f.name, size: f.size, dataBase64: b64FromBytes(bytes) })
        }
        resolve(out)
      }
      input.click()
    })
  },
  async openAppWindow(url) {
    window.open(url, '_blank', 'noopener') // a tab instead of a dedicated desktop window
    return { ok: true }
  }
}

/** The host surface: the Electron bridge on desktop, browser equivalents on the web. */
export const platform: AgentdPlatform = bridge ?? webPlatform
