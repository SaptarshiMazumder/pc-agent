// Run after `npm run build`: electron scripts/discovery-smoke.cjs
// A fresh hidden desktop renderer must show Google even when renderer network
// access to the platform is blocked. Auth/daemon IPC is stubbed; no login occurs.
const { app, BrowserWindow, ipcMain } = require('electron')
const { buildSync } = require('esbuild')
const Module = require('node:module')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
const TOML = require('@iarna/toml')

const desktop = path.resolve(__dirname, '..')
const repo = path.resolve(desktop, '../../..')
const profile = path.join(repo, 'tmp', `desktop-discovery-smoke-${Date.now()}`)
app.setPath('userData', profile)
const source = path.join(desktop, 'src/main/platformDiscovery.ts')
const output = buildSync({ entryPoints: [source], bundle: true, platform: 'node',
  format: 'cjs', write: false }).outputFiles[0].text
const moduleObject = new Module(source, module)
moduleObject._compile(output, source)
const { fetchPlatformDiscovery } = moduleObject.exports
const flavor = TOML.parse(fs.readFileSync(path.join(desktop, 'flavors/core/distribution.toml'), 'utf8'))
const base = flavor.platform.platform_url

setTimeout(() => { console.error('Discovery smoke timed out'); app.exit(1) }, 45_000).unref()
app.whenReady().then(async () => {
  try {
    ipcMain.handle('app:flavor', () => ({ productName: flavor.product.name,
      platformUrl: base, accountsUrl: flavor.platform.accounts_url, storeEnabled: true }))
    ipcMain.handle('app:platformDiscovery', () => fetchPlatformDiscovery(base))
    ipcMain.handle('auth:request', () => ({ status: 200, body: { state: 'signed_out' } }))
    ipcMain.handle('supervisor:status', () => ({ phase: 'running', message: 'smoke test' }))
    ipcMain.handle('supervisor:ensure', () => ({ url: 'ws://127.0.0.1:9', pid: 0 }))
    const win = new BrowserWindow({ show: false, webPreferences: {
      preload: path.join(desktop, 'out/preload/index.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false } })
    win.webContents.session.webRequest.onBeforeRequest(
      { urls: [`${new URL(base).origin}/*`] }, (_details, callback) => callback({ cancel: true }))
    const live = await fetchPlatformDiscovery(base)
    assert(live?.providers?.some(provider => provider.id === 'google'), 'Prod must advertise Google')
    await win.loadFile(path.join(desktop, 'out/renderer/index.html'))
    await win.webContents.executeJavaScript("localStorage.setItem('agentd.mode', 'cloud')")
    await win.loadFile(path.join(desktop, 'out/renderer/index.html'))
    const deadline = Date.now() + 15_000
    let buttons = []
    while (Date.now() < deadline) {
      buttons = await win.webContents.executeJavaScript(
        "Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim())")
      if (buttons.some(text => /Google/.test(text))) break
      await new Promise(resolve => setTimeout(resolve, 200))
    }
    assert(buttons.some(text => /Google/.test(text)), `Google button missing: ${JSON.stringify(buttons)}`)
    console.log('PASS: fresh desktop shows Google with renderer platform requests blocked')
    app.exit(0)
  } catch (error) {
    console.error(error.message)
    app.exit(1)
  }
})
