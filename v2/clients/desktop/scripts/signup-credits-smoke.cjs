// Real admin panel + client + native preload/transport against an isolated HTTP service.
// No production credentials, users, or balances are touched.
const { app, BrowserWindow, ipcMain } = require('electron')
const { buildSync } = require('esbuild')
const Module = require('node:module')
const http = require('node:http')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
const desktop = path.resolve(__dirname, '..')
const repo = path.resolve(desktop, '../../..')
app.setPath('userData', path.join(repo, 'tmp', `welcome-smoke-${Date.now()}`))
const source = path.join(desktop, 'src/main/adminTransport.ts')
const native = new Module(source, module)
native._compile(buildSync({ entryPoints: [source], bundle: true, platform: 'node',
  format: 'cjs', write: false }).outputFiles[0].text, source)
const { requestAdmin } = native.exports
let policy = { enabled: true, credits: 100000, expiry_days: 30, updated_at: 1, updated_by: 'bootstrap' }
let writes = 0
const server = http.createServer(async (request, response) => {
  assert.equal(request.headers.authorization, 'Bearer isolated-smoke-token')
  assert.equal(request.headers['x-internal-key'], undefined)
  response.setHeader('Content-Type', 'application/json')
  if (request.url !== '/admin/signup-credits') { response.writeHead(404); response.end('{}'); return }
  if (request.method === 'POST') {
    let body = ''
    for await (const chunk of request) body += chunk
    policy = { ...policy, ...JSON.parse(body), updated_at: policy.updated_at + 1 }
    writes++
  }
  response.end(JSON.stringify(policy))
})
setTimeout(() => app.exit(1), 30_000).unref()
app.whenReady().then(async () => {
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const base = `http://127.0.0.1:${server.address().port}`
    for (const [url, init] of [
      ['http://example.invalid/admin/signup-credits', {}],
      [`${base}/me/credits`, {}], [`${base}/admin/../me/credits`, {}],
      [`${base}/admin/signup-credits`, { method: 'DELETE' }],
      [`http://name:pass@127.0.0.1:${server.address().port}/admin/signup-credits`, {}]
    ]) await assert.rejects(() => requestAdmin(base, url, init), /Not a configured admin endpoint/)
    ipcMain.handle('admin:request', (_event, url, init) => requestAdmin(base, url, init))
    ipcMain.handle('auth:request', () => ({ status: 200, body: { state: 'ok', accessToken: 'isolated-smoke-token' } }))
    const win = new BrowserWindow({ show: false, width: 1000, height: 800, webPreferences: {
      preload: path.join(desktop, 'out/preload/index.js'), contextIsolation: true,
      nodeIntegration: false, sandbox: false } })
    win.webContents.session.webRequest.onBeforeRequest({ urls: [`${base}/*`] },
      (_details, callback) => callback({ cancel: true }))
    await win.loadURL('about:blank')
    const bundle = buildSync({ stdin: { contents: `
      import React from 'react'; import {createRoot} from 'react-dom/client';
      import {configureAccounts} from '../ui/src/lib/auth';
      import {SignupCreditsPanel} from '../ui/src/components/AdminView';
      configureAccounts(${JSON.stringify(base)});
      createRoot(document.getElementById('root')).render(React.createElement(SignupCreditsPanel));
    `, resolveDir: desktop, sourcefile: 'welcome-smoke.tsx', loader: 'tsx' },
      bundle: true, platform: 'browser', format: 'iife', jsx: 'automatic', write: false,
      define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env': '{}' }
    }).outputFiles[0].text
    const css = fs.readFileSync(path.resolve(desktop, '../ui/src/styles.css'), 'utf8')
    await win.webContents.executeJavaScript(`document.body.innerHTML='<div id="root"></div>';
      const style=document.createElement('style');style.textContent=${JSON.stringify(css)};document.head.appendChild(style)`)
    await win.webContents.executeJavaScript(bundle)
    async function waitFor(expression) {
      for (let n = 0; n < 100; n++) {
        if (await win.webContents.executeJavaScript(expression)) return
        await new Promise(resolve => setTimeout(resolve, 50))
      }
      throw new Error(`UI did not reach expected state: ${expression}`)
    }
    await waitFor("document.getElementById('signup-credit-amount')?.value === '100000'")
    await win.webContents.executeJavaScript("document.querySelector('[role=switch]').click()")
    await win.webContents.executeJavaScript("document.querySelector('button[type=submit]').click()")
    await waitFor("document.body.textContent.includes('Future signup grants are off.')")
    assert.equal(policy.enabled, false)
    await win.webContents.executeJavaScript(`
      const input=document.getElementById('signup-credit-amount');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'250000');
      input.dispatchEvent(new Event('input',{bubbles:true}));`)
    await win.webContents.executeJavaScript("document.querySelector('[role=switch]').click()")
    await win.webContents.executeJavaScript("document.querySelector('button[type=submit]').click()")
    await waitFor("document.body.textContent.includes('250,000 credits once.') && document.getElementById('signup-credit-amount').value === '250000' && document.querySelector('button[type=submit]').disabled")
    assert.equal(policy.credits, 250000)
    assert.equal(policy.enabled, true)
    await win.webContents.executeJavaScript("document.querySelector('[role=switch]').click()")
    await win.webContents.executeJavaScript("document.querySelector('button[type=button]').click()")
    await waitFor("document.querySelector('[role=switch]').checked && document.getElementById('signup-credit-amount').value === '250000' && document.querySelector('button[type=submit]').disabled")
    assert.equal(writes, 2, 'Cancel must never save')
    await win.webContents.executeJavaScript(`window.agentd.adminRequest(${JSON.stringify(base + '/admin/signup-credits')},
      {headers:{Authorization:'Bearer isolated-smoke-token','X-Internal-Key':'must-be-dropped'}})`)
    const png = await win.webContents.capturePage()
    fs.writeFileSync(path.join(repo, 'tmp', 'signup-credits-admin.png'), png.toPNG())
    console.log('PASS: native admin load, disable, change amount, enable, cancel unsaved edits, origin/method/header restrictions')
    server.close()
    app.exit(0)
  } catch (error) { console.error(error.stack); server.close(); app.exit(1) }
})
