import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vite'

/**
 * DEV ONLY: hand the browser client the live local daemon's url+token, read straight from
 * ~/.agentd/gateway.json on each request. This is what lets you open the plain dev URL
 * (http://localhost:5273/) with NO token in it — and keep working across daemon restarts
 * (which rotate the token). Never ships to production: it exists only on the dev server.
 */
function devDaemonToken(): Plugin {
  return {
    name: 'agentd-dev-daemon-token',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__agentd_dev/gateway', (_req, res) => {
        res.setHeader('Content-Type', 'application/json')
        res.setHeader('Cache-Control', 'no-store')
        try {
          const gw = JSON.parse(readFileSync(join(homedir(), '.agentd', 'gateway.json'), 'utf-8'))
          res.end(JSON.stringify({ url: `ws://127.0.0.1:${gw.port || 8787}`, token: gw.token }))
        } catch {
          res.statusCode = 503
          res.end(JSON.stringify({ error: 'daemon not running (no ~/.agentd/gateway.json)' }))
        }
      })
    }
  }
}

/**
 * Widen the index.html CSP for a HOSTED build. The source CSP only allows localhost (great for
 * desktop + local dev), so a browser served from a real host would REFUSE to connect to the
 * accounts/daemon origins. We derive those origins from the SAME env the client bakes in
 * (VITE_AGENTD_ACCOUNTS_URL, VITE_AGENTD_URL) and append them to the fetch/media directives —
 * nothing hardcoded, so any host (ALB now, a domain later) is allowed automatically. Build-only,
 * so the dev server and its localhost CSP are untouched.
 */
function cspAllowApiOrigins(): Plugin {
  const origins = new Set<string>()
  const add = (u?: string): void => {
    if (!u) return
    try {
      const url = new URL(u)
      origins.add(`${url.protocol}//${url.host}`) // as given (ws:// or http://)
      // http(s) equivalent of a ws(s):// url, for <img>/<video>/file fetches off the daemon
      const httpish = url.protocol === 'wss:' ? 'https:' : url.protocol === 'ws:' ? 'http:' : url.protocol
      origins.add(`${httpish}//${url.host}`)
    } catch {
      /* ignore malformed urls */
    }
  }
  add(process.env.VITE_AGENTD_ACCOUNTS_URL)
  add(process.env.VITE_AGENTD_URL)
  const hosts = Array.from(origins).join(' ')
  return {
    name: 'agentd-csp-allow-api-origins',
    apply: 'build',
    transformIndexHtml(html) {
      if (!hosts) return html
      // stop each directive at ; OR the closing " (the last directive has no trailing ;)
      return html.replace(/(connect-src|img-src|media-src|frame-src)([^;"]*)/g, `$1$2 ${hosts}`)
    }
  }
}

/**
 * Standalone WEB build of the JARVIS renderer — the SAME React app the desktop shell runs,
 * served as a plain browser client with no Electron. Host capabilities (supervisor, files)
 * come from src/renderer/src/lib/platform.ts, which falls back to browser equivalents when
 * the Electron bridge (window.agentd) is absent.
 *
 *   npm run dev:web      # dev server; open with ?token=<daemon token> for a localhost daemon
 *   npm run build:web    # static bundle -> dist-web/  (hosted on S3/CloudFront later)
 *
 * Endpoint: the client dials ws://localhost:8787 by default; override with ?daemon=, ?url=
 * + ?token=, or VITE_AGENTD_URL / VITE_AGENTD_TOKEN. Same-origin wss when served from a real
 * host (production).
 */
export default defineConfig({
  root: fileURLToPath(new URL('./src/renderer', import.meta.url)),
  base: './',
  plugins: [react(), devDaemonToken(), cspAllowApiOrigins()],
  build: {
    outDir: fileURLToPath(new URL('./dist-web', import.meta.url)),
    emptyOutDir: true
  },
  server: { port: 5273 }
})
