// Run after `npm run build`: electron scripts/discovery-smoke.cjs
// A fresh hidden renderer must show Google and fetch billing even when renderer
// network access to the platform is blocked. No login or purchase occurs.
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
const billingSource = path.join(desktop, 'src/main/billingTransport.ts')
const billingModule = new Module(billingSource, module)
billingModule._compile(buildSync({ entryPoints: [billingSource], bundle: true,
  platform: 'node', format: 'cjs', write: false }).outputFiles[0].text, billingSource)
const { requestBilling } = billingModule.exports
const billingClient = buildSync({
  entryPoints: [path.resolve(desktop, '../billing/src/billing-client.ts')], bundle: true,
  platform: 'browser', format: 'iife', globalName: 'BillingSmoke', write: false
}).outputFiles[0].text
const flavor = TOML.parse(fs.readFileSync(path.join(desktop, 'flavors/core/distribution.toml'), 'utf8'))
const base = flavor.platform.platform_url

setTimeout(() => { console.error('Discovery smoke timed out'); app.exit(1) }, 45_000).unref()
app.whenReady().then(async () => {
  try {
    await assert.rejects(() => requestBilling(base, 'https://example.invalid/products'),
      /Not a configured billing endpoint/)
    await assert.rejects(() => requestBilling(base, `${base}/admin/accounts`),
      /Not a configured billing endpoint/)
    ipcMain.handle('app:flavor', () => ({ productName: flavor.product.name,
      platformUrl: base, accountsUrl: flavor.platform.accounts_url, storeEnabled: true }))
    ipcMain.handle('app:platformDiscovery', () => fetchPlatformDiscovery(base))
    ipcMain.handle('billing:request', (_event, url, init) => requestBilling(base, url, init))
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
    const catalog = await win.webContents.executeJavaScript(`${billingClient}\n(async () => {
      const client = new BillingSmoke.BillingClient({ accountsUrl: () => ${JSON.stringify(base)},
        accessToken: () => '', newKey: () => 'smoke-no-purchase' });
      const started = performance.now(); const result = await client.catalog();
      return result ? { packs: result.packs.length, durationMs: performance.now() - started } : null;
    })()`)
    assert(catalog, 'Native billing catalog should load with renderer requests blocked')
    console.log(`PASS: native billing catalog loaded in ${Math.round(catalog.durationMs)}ms (${catalog.packs} packs)`)
    const agentWindow = new BrowserWindow({ show: false, webPreferences: {
      preload: path.join(desktop, 'out/preload/app.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false } })
    await agentWindow.loadURL('about:blank')
    const agentCatalog = await agentWindow.webContents.executeJavaScript(`${billingClient}\n(async () => {
      const client = new BillingSmoke.BillingClient({ accountsUrl: () => ${JSON.stringify(base)},
        accessToken: () => '', newKey: () => 'smoke-no-purchase' });
      const result = await client.catalog(); return result ? result.packs.length : null;
    })()`)
    assert.notEqual(agentCatalog, null, 'Agent-window billing bridge must work too')
    console.log(`PASS: agent-window billing loaded ${agentCatalog} packs`)
    app.exit(0)
  } catch (error) {
    console.error(error.message)
    app.exit(1)
  }
})
