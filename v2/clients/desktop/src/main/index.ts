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
import { connectUrl } from './rendezvous'
import { Supervisor } from './supervisor'

let flavor: Flavor = {
  productId: 'agentd', productName: 'agentd', defaultAgent: '', storeEnabled: true,
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
  supervisor.onStatus((status) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('supervisor:status-changed', status)
    }
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
  createWindow()
  // kick the daemon in the background immediately — the renderer shows live status
  // and connects the moment it's up
  void supervisor.ensure().catch(() => {})
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// The daemon is a USER-level service (cron, channels) — closing the shell must not
// kill it. Quit the shell like a normal app; the daemon keeps serving.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
