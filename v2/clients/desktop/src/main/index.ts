/**
 * Desktop shell main process — window + composition root.
 *
 * Owns exactly three concerns and delegates each: which PRODUCT this build is
 * (flavor.ts), whether a daemon is running and how to start one (supervisor.ts),
 * and the browser window. The renderer gets everything over three IPC surfaces:
 * flavor (branding), supervisor status (streamed), and the gateway connect URL.
 * All agent intelligence stays in the daemon — this process never talks to an LLM.
 */

import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import fs from 'node:fs'
import path from 'node:path'

import { Flavor, loadFlavor } from './flavor'
import { gatewayRequest } from './gatewayRpc'
import { connectUrl } from './rendezvous'
import { Supervisor } from './supervisor'

let flavor: Flavor = {
  productId: 'agentd', productName: 'agentd', defaultAgent: '', appAgent: '', storeEnabled: true,
  preinstalledBundles: [], sourcePath: '', bundledPackages: []
}
const supervisor = new Supervisor(() => flavor.sourcePath)
let mainWindow: BrowserWindow | null = null

// The nakama-link app icon (green, transparent). In dev __dirname is out/main, so
// ../../resources reaches the project's resources/; packaged builds get it from
// electron-builder's win.icon + the exe, but the explicit path keeps dev identical.
const appIcon = path.join(
  __dirname,
  '../../resources',
  process.platform === 'win32' ? 'icon.ico' : 'icon.png'
)

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: flavor.productName,
    icon: appIcon,
    backgroundColor: '#f4f2ea',   // matches the LIGHT theme surface (the default theme)
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })
  mainWindow.on('closed', () => (mainWindow = null))
  // External links open in the SYSTEM browser, never inside the shell — via BOTH
  // escape routes: window.open/target=_blank hits setWindowOpenHandler, while a
  // plain <a href> (e.g. a markdown link in chat) navigates the window itself and
  // only fires will-navigate. Only http(s) is handed to the OS (no shell-opening
  // of arbitrary protocols), and everything foreign is blocked either way.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  const appOrigin = process.env.ELECTRON_RENDERER_URL
    ? new URL(process.env.ELECTRON_RENDERER_URL).origin
    : 'file:'
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url.startsWith(appOrigin)) return              // our own app (dev reloads)
    event.preventDefault()
    if (/^https?:/i.test(url)) void shell.openExternal(url)
  })
  if (process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'))
  }
}

function registerIpc(): void {
  ipcMain.handle('app:flavor', () => ({
    productId: flavor.productId,
    productName: flavor.productName,
    defaultAgent: flavor.defaultAgent,
    appAgent: flavor.appAgent,
    storeEnabled: flavor.storeEnabled,
    preinstalledBundles: flavor.preinstalledBundles,
    bundledPackages: flavor.bundledPackages,
    version: app.getVersion()
  }))
  ipcMain.handle('supervisor:status', () => supervisor.current())
  ipcMain.handle('supervisor:ensure', async () => {
    const info = await supervisor.ensure()
    return { url: connectUrl(info), version: info.version, pid: info.pid }
  })
  // restart the daemon so restart-gated config changes actually take effect (the renderer
  // triggers this after saving such settings; the gateway then auto-reconnects to the new one)
  ipcMain.handle('supervisor:restart', async () => {
    const info = await supervisor.restart()
    return { url: connectUrl(info), version: info.version, pid: info.pid }
  })
  supervisor.onStatus((status) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('supervisor:status-changed', status)
    }
  })

  // --- agent apps: open an APP AGENT's daemon-served UI (/apps/<id>/) in its OWN window
  //     (shared with the agent-app shell boot below — same window either way).
  ipcMain.handle('app:openWindow', (_e, rawUrl: string, title?: string) => {
    let url: URL
    try {
      url = new URL(String(rawUrl || ''))
    } catch {
      return { ok: false, error: 'invalid url' }
    }
    if (!/^https?:$/.test(url.protocol)) return { ok: false, error: 'http(s) urls only' }
    openAgentAppWindow(url, title)
    return { ok: true }
  })

  // --- local files: open in the OS default app / reveal in the file manager, and the
  //     attachment picker (reads the chosen files so the renderer can upload their bytes)
  ipcMain.handle('file:open', (_e, p: string) => shell.openPath(String(p || '')))
  ipcMain.handle('file:reveal', (_e, p: string) => {
    shell.showItemInFolder(String(p || ''))
  })
  ipcMain.handle('file:pick', async () => {
    const win = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0]
    const res = win
      ? await dialog.showOpenDialog(win, { properties: ['openFile', 'multiSelections'] })
      : await dialog.showOpenDialog({ properties: ['openFile', 'multiSelections'] })
    if (res.canceled) return []
    return readPicked(res.filePaths)
  })
  // save a COPY of a produced deliverable wherever the user chooses (defaults to their
  // Downloads folder + the original filename) — the local-first equivalent of a download
  ipcMain.handle('file:download', async (_e, p: string) => {
    const src = String(p || '')
    try {
      if (!fs.statSync(src).isFile()) return { ok: false, error: 'not a file' }
    } catch {
      return { ok: false, error: 'not found' }
    }
    const win = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0]
    const defaultPath = path.join(app.getPath('downloads'), path.basename(src))
    const res = win
      ? await dialog.showSaveDialog(win, { defaultPath })
      : await dialog.showSaveDialog({ defaultPath })
    if (res.canceled || !res.filePath) return { ok: false, canceled: true }
    try {
      await fs.promises.copyFile(src, res.filePath)
      return { ok: true, path: res.filePath }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  })
  // read a local file's TEXT for the Canvas viewer/editor (IPC, not HTTP — avoids the
  // cross-origin fetch restriction the /file endpoint would hit)
  ipcMain.handle('file:read', async (_e, p: string) => {
    try {
      return { ok: true, text: await fs.promises.readFile(String(p || ''), 'utf8') }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  })
  // read a local file's BYTES as base64 — the raster editor loads it as a data: URL so
  // the fabric canvas stays same-origin (untainted) and can export a PNG back
  ipcMain.handle('file:readBytes', async (_e, p: string) => {
    try {
      const buf = await fs.promises.readFile(String(p || ''))
      return { ok: true, base64: buf.toString('base64') }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  })
  // save edited content back to an EXISTING file (Canvas editor). Text by default; pass
  // base64=true to write binary (edited PNG). Refuses to create new files.
  ipcMain.handle('file:save', async (_e, p: string, content: string, base64?: boolean) => {
    const target = String(p || '')
    try {
      if (!fs.statSync(target).isFile()) return { ok: false, error: 'not a file' }
      const data = base64 ? Buffer.from(String(content || ''), 'base64') : String(content ?? '')
      await fs.promises.writeFile(target, data)
      return { ok: true }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  })
  // save EDITED content to a file the user picks (export a copy) — text or base64 binary.
  ipcMain.handle('file:saveAs', async (_e, defaultName: string, content: string, base64?: boolean) => {
    const win = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0]
    const defaultPath = path.join(app.getPath('downloads'), String(defaultName || 'export'))
    const res = win
      ? await dialog.showSaveDialog(win, { defaultPath })
      : await dialog.showSaveDialog({ defaultPath })
    if (res.canceled || !res.filePath) return { ok: false, canceled: true }
    try {
      const data = base64 ? Buffer.from(String(content || ''), 'base64') : String(content ?? '')
      await fs.promises.writeFile(res.filePath, data)
      return { ok: true, path: res.filePath }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e) }
    }
  })
}

// --- agent-app windows ---------------------------------------------------------------
// The page is remote content — no preload, no node, sandboxed — it talks to the daemon
// itself over WebSocket exactly like a browser tab would. One window per app path,
// reused on relaunch (the token in the query may have rotated). Shared by the
// app:openWindow IPC (desktop client's "Open app") and the agent-app shell boot.
const appWindows = new Map<string, BrowserWindow>()

function openAgentAppWindow(url: URL, title?: string): BrowserWindow {
  const key = `${url.origin}${url.pathname}`
  const existing = appWindows.get(key)
  if (existing && !existing.isDestroyed()) {
    void existing.loadURL(url.toString()) // re-mint token/scope, then surface it
    if (existing.isMinimized()) existing.restore()
    existing.focus()
    return existing
  }
  const win = new BrowserWindow({
    width: 1100,
    height: 800,
    minWidth: 480,
    minHeight: 360,
    title: String(title || 'agent app'),
    icon: appIcon,
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  })
  appWindows.set(key, win)
  win.on('closed', () => appWindows.delete(key))
  // same escape routes as the main window: anything beyond the daemon's own origin
  // opens in the SYSTEM browser, never inside the shell
  win.webContents.setWindowOpenHandler(({ url: u }) => {
    if (/^https?:/i.test(u)) void shell.openExternal(u)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event, u) => {
    if (u.startsWith(url.origin)) return // the app's own SPA routes
    event.preventDefault()
    if (/^https?:/i.test(u)) void shell.openExternal(u)
  })
  void win.loadURL(url.toString())
  return win
}

// --- agent-app SHELL mode (distribution.toml: [product] app_agent) ---------------------
// This build IS one agent's product: no JARVIS renderer, ever. Boot = splash → find-or-
// start the daemon (reuse whatever the machine already runs) → first-run install of the
// bundled .agentpkg → open the agent's own UI in its window. The shell only CONSUMES the
// daemon's published protocol — zero backend involvement.

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function createSplash(): BrowserWindow {
  const splash = new BrowserWindow({
    width: 380,
    height: 200,
    resizable: false,
    autoHideMenuBar: true,
    title: flavor.productName,
    icon: appIcon,
    backgroundColor: '#f4f2ea',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  })
  const name = escapeHtml(flavor.productName)
  const html =
    `<!doctype html><html><head><meta charset="utf-8"><title>${name}</title>` +
    `<style>body{font-family:system-ui,sans-serif;background:#f4f2ea;color:#1a1a1a;` +
    `display:grid;place-items:center;height:100vh;margin:0}main{text-align:center;padding:0 24px}` +
    `h1{font-size:17px;margin:0 0 8px}p{font-size:13px;color:#666;margin:0}</style></head>` +
    `<body><main><h1>${name}</h1><p id="s">starting…</p></main>` +
    `<script>window.__status=function(t){document.getElementById('s').textContent=t}</script>` +
    `</body></html>`
  void splash.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
  return splash
}

/** First-run: install any bundled .agentpkg the daemon doesn't have yet — the same flow
 *  the JARVIS renderer runs (store.ts preinstallBundles), mirrored here because an
 *  app-shell build never loads that renderer. Idempotent via the installed ledger. */
async function ensureBundlesInstalled(wsUrl: string): Promise<void> {
  if (flavor.bundledPackages.length === 0) return
  const { bundles } = await gatewayRequest(wsUrl, 'marketplace.installed')
  const have = new Set(((bundles as Array<{ id: string }>) || []).map((b) => b.id))
  for (const packagePath of flavor.bundledPackages) {
    const fileName = packagePath.replace(/\\/g, '/').split('/').pop() || ''
    const bundleId = fileName.replace(/-[0-9][^-]*\.agentpkg$/, '')
    if (!bundleId || have.has(bundleId)) continue
    // generous timeout: an install may pip-install the agent's plugin deps
    await gatewayRequest(wsUrl, 'marketplace.install', { file: packagePath }, 600_000)
  }
}

async function launchAgentApp(agentId: string): Promise<void> {
  const splash = createSplash()
  const status = (t: string): void => {
    if (splash.isDestroyed()) return
    void splash.webContents
      .executeJavaScript(`window.__status && window.__status(${JSON.stringify(t)})`)
      .catch(() => {})
  }
  supervisor.onStatus((s) => status(s.message))
  try {
    const info = await supervisor.ensure()
    const wsUrl = connectUrl(info)
    status('preparing the agent…')
    await ensureBundlesInstalled(wsUrl)
    // open the ADVERTISED app surface — never assume the URL shape
    const { agents } = await gatewayRequest(wsUrl, 'agents.list')
    const entry = ((agents as Array<{ id: string; app?: { url?: string; title?: string } | null }>) || []).find(
      (a) => a.id === agentId
    )
    if (!entry?.app?.url) throw new Error(`agent '${agentId}' is not installed or has no app UI`)
    const q = new URLSearchParams({ scope: `agent:${agentId}` })
    if (info.token) q.set('token', info.token)
    const url = new URL(`http://${info.host}:${info.port}${entry.app.url}?${q.toString()}`)
    const win = openAgentAppWindow(url, entry.app.title || flavor.productName)
    win.webContents.once('did-finish-load', () => {
      if (!splash.isDestroyed()) splash.close()
    })
  } catch (e) {
    status(`could not start: ${String((e as Error)?.message || e)}`)
  }
}

const UPLOAD_MAX = 32 * 1024 * 1024 // mirror the daemon's UPLOAD_MAX_BYTES

/** Read chosen file paths into upload payloads (name + base64 bytes). Oversized or
 *  unreadable files are skipped so one bad pick never breaks the batch. */
function readPicked(paths: string[]): Array<{ name: string; size: number; dataBase64: string }> {
  const out: Array<{ name: string; size: number; dataBase64: string }> = []
  for (const p of paths) {
    try {
      const stat = fs.statSync(p)
      if (!stat.isFile() || stat.size > UPLOAD_MAX) continue
      out.push({ name: path.basename(p), size: stat.size, dataBase64: fs.readFileSync(p).toString('base64') })
    } catch {
      /* skip unreadable file */
    }
  }
  return out
}

app.whenReady().then(async () => {
  flavor = await loadFlavor()
  registerIpc()
  if (flavor.appAgent) {
    // AGENT-APP shell: this build IS one agent's product — its own UI, no JARVIS renderer
    void launchAgentApp(flavor.appAgent)
  } else {
    createWindow()
    // kick the daemon in the background immediately — the renderer shows live status
    // and connects the moment it's up
    void supervisor.ensure().catch(() => {})
  }
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length !== 0) return
    if (flavor.appAgent) void launchAgentApp(flavor.appAgent)
    else createWindow()
  })
})

// The daemon is a USER-level service (cron, channels) — closing the shell must not
// kill it. Quit the shell like a normal app; the daemon keeps serving.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
