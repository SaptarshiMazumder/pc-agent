/**
 * Desktop shell main process — window + composition root.
 *
 * Owns exactly three concerns and delegates each: which PRODUCT this build is
 * (flavor.ts), whether a daemon is running and how to start one (supervisor.ts),
 * and the browser window. The renderer gets everything over three IPC surfaces:
 * flavor (branding), supervisor status (streamed), and the gateway connect URL.
 * All agent intelligence stays in the daemon — this process never talks to an LLM.
 */

import { app, BrowserWindow, ipcMain, shell } from 'electron'
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

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: flavor.productName,
    backgroundColor: '#0f1115',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })
  mainWindow.on('closed', () => (mainWindow = null))
  // external links open in the system browser, never inside the shell
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
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
