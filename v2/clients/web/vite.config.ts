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
  /** Every port on this url's HOSTNAME, in both the http and ws families. */
  const addAllPorts = (u?: string): void => {
    if (!u) return
    try {
      const { protocol, hostname } = new URL(u)
      const secure = protocol === 'https:' || protocol === 'wss:'
      origins.add(`${secure ? 'https' : 'http'}://${hostname}:*`)
      origins.add(`${secure ? 'wss' : 'ws'}://${hostname}:*`)
    } catch {
      /* ignore malformed urls */
    }
  }
  // THE PLATFORM HOST, EVERY PORT. A CSP is a BUILD-TIME allowlist — it has to be in the
  // document before the first request — so it cannot be discovered the way addresses now
  // are. Naming each service's port here would put the rot back exactly where discovery
  // just removed it: the daemon moves to a new port, or a service is added, and the
  // symptom is a blocked request that looks like the backend is down.
  //
  // So: allow the platform's own host on any port, for both the http and ws families. It
  // is OUR host — every service on it is one we deployed — and `:*` is valid CSP
  // host-source syntax. Narrower than it looks, and it never needs editing again.
  addAllPorts(process.env.VITE_AGENTD_PLATFORM_URL)
  // The overrides, when a build sets them (local compose, dev.py). Exact origins, because
  // those are arbitrary hosts rather than our deployment.
  add(process.env.VITE_AGENTD_ACCOUNTS_URL)
  add(process.env.VITE_AGENTD_URL)
  // RUM posts here. Without this the CSP blocks every report on a hosted build, and the only
  // symptom is a permanently empty dashboard — the failure mode this whole plugin exists for.
  add(process.env.VITE_AGENTD_INGEST_URL)
  const hosts = Array.from(origins).join(' ')
  return {
    name: 'agentd-csp-allow-api-origins',
    apply: 'build',
    transformIndexHtml(html) {
      if (!hosts) return html
      // SCOPED TO THE TAG'S content ATTRIBUTE. The previous version ran the directive regex over
      // the WHOLE document, which also matched the words "connect-src" in the prose comment above
      // the tag — and that match's `[^;"]*` ran on past `-->` and terminated at the quote in
      // `http-equiv="`, injecting the hosts INTO that attribute:
      //
      //   <meta http-equiv= http://alb:4100 …"Content-Security-Policy"  content="…">
      //
      // A browser then reads http-equiv as `http://alb:4100`, so it is never
      // "Content-Security-Policy" and THE WHOLE POLICY IS INERT. Every hosted build shipped with
      // no CSP at all, and the symptom was the absence of a symptom: the app worked fine, because
      // nothing was being restricted. Isolate the attribute first, edit only inside it.
      const CSP_TAG = /(<meta[^>]*http-equiv=["']Content-Security-Policy["'][^>]*content=")([^"]*)(")/i
      if (!CSP_TAG.test(html)) {
        // Fail LOUDLY rather than silently ship a build whose API origins are not allowed —
        // that produces a client that loads and then cannot reach anything.
        throw new Error(
          'agentd-csp-allow-api-origins: no <meta http-equiv="Content-Security-Policy" content="…"> ' +
            'found in an entry document. EVERY entry needs one — this hook runs per document, so a ' +
            'new entry added without a policy tag would otherwise ship unable to reach the API. ' +
            'The hosted build needs its API origins in the policy; refusing to emit.'
        )
      }
      return html.replace(CSP_TAG, (_all, before: string, policy: string, after: string) => {
        // Inside the attribute there are no quotes, so a directive ends at `;` or at the end of
        // the value (the last directive has no trailing `;`).
        const widened = policy.replace(
          /(connect-src|img-src|media-src|frame-src)([^;]*)/g,
          `$1$2 ${hosts}`
        )
        return before + widened + after
      })
    }
  }
}

/**
 * Standalone WEB build of the JARVIS renderer.
 *
 * `root` points at the SHARED ui package (../ui) — the exact same sources electron.vite.config.ts
 * builds for the desktop shell. There is no second copy of the UI and no per-target fork: host
 * capabilities come from ui/src/lib/platform.ts, which falls back to browser equivalents when the
 * Electron bridge (window.agentd) is absent.
 *
 *   npm run dev -w agentd-web      # dev server on :5273
 *   npm run build -w agentd-web    # static bundle -> web/dist/ (served by nginx, see Dockerfile)
 *
 * Endpoint: the client dials ws://localhost:8787 by default; override with ?daemon=, ?url=
 * + ?token=, or VITE_AGENTD_URL / VITE_AGENTD_TOKEN. Same-origin wss when served from a real
 * host (production).
 */
export default defineConfig({
  root: fileURLToPath(new URL('../ui', import.meta.url)),
  base: './',
  plugins: [react(), devDaemonToken(), cspAllowApiOrigins()],
  build: {
    outDir: fileURLToPath(new URL('./dist', import.meta.url)),
    emptyOutDir: true,
    // TWO DOCUMENTS, not two builds. The app and the admin console are separate entries so the
    // console ships without the chat shell and can own a hostname of its own — but they share
    // this build, this dependency graph and this output directory, so the code they genuinely
    // have in common (auth, the gateway client, styles) is emitted once and cached once.
    //
    // `base: './'` above is what lets the same admin.html work at BOTH /admin (today) and at the
    // root of admin.<domain> (once DNS exists): relative asset urls resolve against whichever
    // directory the document was served from. nginx serves /admin without a trailing slash for
    // exactly that reason — see web/nginx.conf.template.
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('../ui/index.html', import.meta.url)),
        admin: fileURLToPath(new URL('../ui/admin.html', import.meta.url))
      }
    }
  },
  server: { port: 5273 }
})
