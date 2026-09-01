/**
 * Desktop shell main process — window + composition root.
 *
 * Owns exactly three concerns and delegates each: which PRODUCT this build is
 * (flavor.ts), whether a daemon is running and how to start one (supervisor.ts),
 * and the browser window. The renderer gets everything over three IPC surfaces:
 * flavor (branding), supervisor status (streamed), and the gateway connect URL.
 * All agent intelligence stays in the daemon — this process never talks to an LLM.
 */

import { app, BrowserWindow, dialog, ipcMain, safeStorage, shell } from 'electron'
import fs from 'node:fs'
import path from 'node:path'

import { Flavor, loadFlavor } from './flavor'
import { gatewayRequest } from './gatewayRpc'
import { connectUrl } from './rendezvous'
import { Supervisor } from './supervisor'

let flavor: Flavor = {
  productId: 'agentd', productName: 'agentd', defaultAgent: '', appAgent: '', storeEnabled: true,
  preinstalledBundles: [], platformUrl: '', accountsUrl: '', modelProxyUrl: '', sourcePath: '', bundledPackages: [],
  payloadDir: '', iconPath: '', appUserModelId: ''
}
const supervisor = new Supervisor(() => flavor.sourcePath)
let mainWindow: BrowserWindow | null = null

// The nakama-link app icon (green, transparent). In dev __dirname is out/main, so
// ../../resources reaches the project's resources/; packaged builds get it from
// electron-builder's win.icon + the exe, but the explicit path keeps dev identical.
const engineIcon = path.join(
  __dirname,
  '../../resources',
  process.platform === 'win32' ? 'icon.ico' : 'icon.png'
)

/** The icon for THIS product. One engine serves many products (flavor.payloadDir), so the
 *  window icon has to come from the payload — the executable's own icon is only the fallback
 *  for the engine's plain desktop client. Resolved per call: flavor loads asynchronously. */
function appIconPath(): string {
  return flavor.iconPath || engineIcon
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: flavor.productName,
    icon: appIconPath(),
    backgroundColor: '#f4f2ea',   // matches the LIGHT theme surface (the default theme)
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // An occluded window must keep streaming: this app's whole job is watching long agent
      // runs while the user looks elsewhere, and Chromium's occlusion throttling is one of the
      // triggers behind mystery mid-run socket drops (2026-08-29).
      backgroundThrottling: false,
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
    platformUrl: flavor.platformUrl,
    accountsUrl: flavor.accountsUrl,
    modelProxyUrl: flavor.modelProxyUrl,
    // Compatibility for older renderers loaded during a rolling desktop update.
    modelGatewayUrl: flavor.modelProxyUrl,
    bundledPackages: flavor.bundledPackages,
    version: app.getVersion()
  }))
  // THE REFRESH TOKEN'S HOME ON DESKTOP. A 30-day credential in localStorage is readable by
  // anything that can read the profile directory; safeStorage wraps it with a key held by the OS
  // keychain (DPAPI on Windows, Keychain on macOS, libsecret on Linux). The ACCESS token is never
  // written anywhere — it is re-derivable from this one, so persisting it would only add a place
  // to steal it from.
  //
  // Falls back to plaintext-on-disk when the OS declines encryption (a headless Linux box with no
  // keyring), because the alternative is an app that silently cannot stay signed in. The file
  // records which it was, so a later read never tries to decrypt something that was never encrypted.
  const secretFile = path.join(app.getPath('userData'), 'refresh-token.json')
  ipcMain.handle('secrets:read', () => {
    try {
      if (!fs.existsSync(secretFile)) return null
      const box = JSON.parse(fs.readFileSync(secretFile, 'utf-8')) as {
        v?: string
        encrypted?: boolean
      }
      if (!box?.v) return null
      if (!box.encrypted) return box.v
      return safeStorage.decryptString(Buffer.from(box.v, 'base64'))
    } catch {
      return null // unreadable or from another machine's keychain => signed out, not crashed
    }
  })
  ipcMain.handle('secrets:write', (_e, token: string | null) => {
    try {
      if (!token) {
        if (fs.existsSync(secretFile)) fs.unlinkSync(secretFile)
        return true
      }
      const canEncrypt = safeStorage.isEncryptionAvailable()
      const box = canEncrypt
        ? { encrypted: true, v: safeStorage.encryptString(token).toString('base64') }
        : { encrypted: false, v: token }
      fs.writeFileSync(secretFile, JSON.stringify(box), { mode: 0o600 })
      return true
    } catch {
      return false
    }
  })

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
  // The shell renderer calls this on every token rotation; app windows have no other way to
  // learn about it, and without it they run until their launch token expires and then go
  // anonymous (which reads to the user as "all my agents disappeared").
  // --- machine sign-in: the renderer's ONLY path to the daemon's /auth/*. The renderer
  //     page is file:// — cross-origin to the daemon, whose HTTP surface (the websockets
  //     handshake hook) speaks no CORS — so a fetch from the page can never read the answer.
  //     Main is a native caller with the same trust as the daemon's own state dir. Paths are
  //     whitelisted and only X-Auth-* headers pass, so this cannot become a generic proxy.
  ipcMain.handle(
    'auth:request',
    async (_e, authPath: string, headers?: Record<string, string>) => {
      if (!/^\/auth\/(token|status|login|logout|adopt)$/.test(String(authPath || ''))) {
        return { status: 400, body: { error: 'not an auth path' } }
      }
      const safe: Record<string, string> = {}
      for (const [k, v] of Object.entries(headers || {})) {
        if (/^X-Auth-[A-Za-z-]+$/.test(k)) safe[k] = String(v)
      }
      try {
        const info = await supervisor.ensure()
        const u = new URL(`http://${info.host}:${info.port}${authPath}`)
        if (info.token) u.searchParams.set('token', info.token)
        const r = await fetch(u, { headers: safe, cache: 'no-store' })
        const body = (await r.json().catch(() => ({}))) as Record<string, unknown>
        return { status: r.status, body }
      } catch (e) {
        // The DAEMON is unreachable — same answer shape as the runtime's own "accounts away":
        // keep working, retry. Never "signed out" over a hiccup.
        return { status: 0, body: { state: 'accounts_unreachable', error: String(e) } }
      }
    }
  )

  ipcMain.handle('app:broadcastToken', (_e, token: string) => {
    broadcastAccessToken(String(token || ''))
    return { ok: true, windows: appWindows.size }
  })

  ipcMain.handle('app:openWindow', (event, rawUrl: string, title?: string) => {
    // WHO IS ASKING, decided here and never by the caller.
    //
    // Two kinds of window can reach this channel. The shell's own renderer is ours and may open
    // anything. An AGENT APP window runs third-party code — the whole premise of the marketplace —
    // and this call takes a URL, so granting it generally would let any published agent open a
    // desktop window pointed at any address it liked, wearing this app's frame.
    //
    // Agent Builder is the exception, because building an agent's window and looking at it are one
    // loop and the second half of it used to live in a different application. It is identified by
    // the WINDOW the request arrived on, which the page cannot state or forge — not by anything in
    // the message.
    if (!mayOpenAppWindows(event.sender)) {
      return { ok: false, error: 'this window may not open app windows' }
    }
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

/** The receive-only bridge for agent app windows (built alongside the shell's own preload). */
function appPreloadPath(): string {
  return path.join(__dirname, '../preload/app.js')
}

/**
 * The one agent app allowed to open other agents' windows.
 *
 * A specific id in the shell, which is not something this codebase does lightly. The justification
 * is the same one the daemon already accepts for this agent (CROSS_AGENT_READS): Agent Builder is
 * not an agent among agents, it is the tool that makes them, and the window it is building is the
 * thing it exists to show you.
 */
const BUILDER_APP_PATH = '/apps/agent-builder/'

/**
 * May this sender open app windows?
 *
 * The shell's own renderer may — it is our code, and it is where the "Open app" button has always
 * lived. An agent app window may only if it IS Agent Builder, decided by matching the sender
 * against the window register rather than by anything the page said about itself.
 *
 * Fails CLOSED: a sender we cannot place is refused. A window that is not in the register is not a
 * window this process opened.
 */
function mayOpenAppWindows(sender: Electron.WebContents): boolean {
  if (mainWindow && !mainWindow.isDestroyed() && sender === mainWindow.webContents) return true
  for (const [key, win] of appWindows) {
    if (!win.isDestroyed() && win.webContents === sender) {
      return new URL(key).pathname === BUILDER_APP_PATH
    }
  }
  return false
}

/**
 * Hand every open agent app window a freshly-minted access token.
 *
 * Called by the SHELL renderer whenever its token rotates. This is the whole reason app windows
 * outlive a single ten-minute token: they cannot refresh (no refresh token, by design), so the
 * one party that can — the shell — pushes the result down.
 */
function broadcastAccessToken(token: string): void {
  for (const win of appWindows.values()) {
    if (!win.isDestroyed()) win.webContents.send('agentd:access-token', token)
  }
}

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
    icon: appIconPath(),
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      // Same reason as the shell window: an agent app streams runs while occluded.
      backgroundThrottling: false,
      // sandbox stays ON. The app preload is receive-only and needs no Node APIs — see
      // src/preload/app.ts for why this window gets its own, much smaller, bridge.
      sandbox: true,
      preload: appPreloadPath()
    }
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
  // Diagnosability: the app page's console is invisible in a packaged build — mirror it to
  // the shell's stdout so a stuck page can always be traced (`"...exe" > log` or dev run).
  win.webContents.on('console-message', (_e, _level, message) => {
    console.log(`[app-page] ${message}`)
  })
  // E2E hook (env-gated, dev diagnosis only): auto-drive the sign-in form so the exact
  // packaged environment can be exercised without a human at the keyboard.
  const e2e = String(process.env.AGENTD_E2E_LOGIN || '')
  if (e2e.includes(':')) {
    win.webContents.on('did-finish-load', () => {
      const [email, pass] = [e2e.slice(0, e2e.indexOf(':')), e2e.slice(e2e.indexOf(':') + 1)]
      // fill + submit (values injected as JSON literals — no interpolation surprises)
      void win.webContents.executeJavaScript(
        `setTimeout(() => {
           const g = document.getElementById('gate')
           console.log('[e2e] gate hidden =', g ? g.hidden : 'no-gate')
           if (g && !g.hidden) {
             document.getElementById('gateEmail').value = ${JSON.stringify(email)}
             document.getElementById('gatePass').value = ${JSON.stringify(pass)}
             document.getElementById('gateForm').requestSubmit()
             console.log('[e2e] submitted sign-in for', ${JSON.stringify(email)})
           }
         }, 1500)`
      ).catch(() => {})
    })
  }
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
    icon: appIconPath(),
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
/** `<id>-<version>.agentpkg` -> its two parts. The version is the artifact's own claim; the
 *  installer re-reads the manifest, so this is only used to decide WHETHER to install. */
function splitPackageName(fileName: string): { id: string; version: string } {
  const m = /^(.*)-([0-9][^-]*)\.agentpkg$/.exec(fileName)
  if (!m) return { id: fileName.replace(/\.agentpkg$/, ''), version: '' }
  return { id: m[1], version: m[2] }
}

/** True when `candidate` is a newer version than `installed`. Numeric-dotted compare with a
 *  string tiebreak, matching how the daemon supersedes installs. An unparseable or equal
 *  version is NOT an update — re-installing on every launch would wipe the agent's directory
 *  (and its user's edits) each time the app opened. */
function isNewer(candidate: string, installed: string): boolean {
  if (!candidate) return false
  if (!installed) return true
  const a = candidate.split('.')
  const b = installed.split('.')
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = parseInt(a[i] ?? '0', 10)
    const y = parseInt(b[i] ?? '0', 10)
    if (Number.isNaN(x) || Number.isNaN(y)) return candidate > installed
    if (x !== y) return x > y
  }
  return false
}

/** First run AND every upgrade: install any bundled .agentpkg the daemon doesn't have, or has
 *  at an OLDER version.
 *
 *  The version check is the point. This used to skip on bundle ID alone, which meant a shipped
 *  app-agent product could never deliver a new version of its own agent: the user installs
 *  "Game Master 0.2.0", you ship 0.3.0 with a fix, they run the new installer — and the shell
 *  sees game-master already present and installs nothing. The app updates, the agent inside it
 *  never does, and the symptom is a fixed bug that is still there.
 *
 *  Equal versions must NOT reinstall: `install_files` replaces the agent's definition, so doing
 *  it on every launch would discard whatever the user changed under agents/<id>/. */
async function ensureBundlesInstalled(wsUrl: string): Promise<void> {
  if (flavor.bundledPackages.length === 0) return
  const { bundles } = await gatewayRequest(wsUrl, 'marketplace.installed')
  const installed = new Map<string, string>(
    ((bundles as Array<{ id: string; version?: string }>) || []).map((b) => [
      b.id,
      String(b.version || '')
    ])
  )
  for (const packagePath of flavor.bundledPackages) {
    const fileName = packagePath.replace(/\\/g, '/').split('/').pop() || ''
    const { id, version } = splitPackageName(fileName)
    if (!id) continue
    const have = installed.get(id)
    if (have !== undefined && !isNewer(version, have)) continue
    console.log(
      have === undefined
        ? `bundles: installing ${id} ${version}`
        : `bundles: upgrading ${id} ${have} -> ${version}`
    )
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
  // Windows taskbar identity. With ONE shared executable the OS cannot tell products apart by
  // path, so without this every agent app collapses into a single taskbar button and a pinned
  // shortcut relaunches whichever product was installed last. Must match the AUMID the
  // launching shortcut sets. No-op on other platforms.
  if (flavor.appUserModelId) app.setAppUserModelId(flavor.appUserModelId)
  if (flavor.payloadDir) {
    console.log(`flavor: product "${flavor.productName}" from payload ${flavor.payloadDir}`)
  }
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
