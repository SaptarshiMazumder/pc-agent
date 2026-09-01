import react from '@vitejs/plugin-react'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { resolve, sep } from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vite'

/**
 * DEV ONLY: serve the LOCAL registry's catalog.json (and its artifacts) at the page's own origin,
 * so `npm run dev -w agentd-marketplace` shows the real store with no AWS, no daemon and no CORS.
 *
 * The directory comes from AGENTD_REGISTRY (the same variable the daemon reads) or --registry, so
 * nothing here is hardcoded to one machine. Without one the page loads and says the registry is
 * empty, which is the honest answer for a dev box that has never published anything.
 *
 * Never ships: `apply: 'serve'`. In production the catalog is a real file at the site's origin,
 * put there by a CloudFront behaviour pointing at the registry bucket.
 */
function devRegistry(): Plugin {
  // `resolve` once, so the containment check below compares two paths of the same shape. A raw
  // env value and a joined one differ by separator on Windows (C:/reg vs C:\reg\x), and a
  // startsWith between them is false for every legitimate file — the guard would reject
  // everything and the store would look permanently empty.
  const dir = process.env.AGENTD_REGISTRY ? resolve(process.env.AGENTD_REGISTRY) : ''
  return {
    name: 'agentd-dev-registry',
    apply: 'serve',
    configureServer(server) {
      if (!dir) {
        server.config.logger.warn(
          '[marketplace] AGENTD_REGISTRY is not set — the page will show an empty registry. ' +
            'Point it at a directory holding catalog.json (any `agentd bundle publish --to <dir>` target).'
        )
        return
      }
      server.middlewares.use((req, res, next) => {
        // Only paths the REGISTRY owns; everything else is the page's own bundle.
        const name = decodeURIComponent((req.url || '').split('?')[0]).replace(/^\/+/, '')
        if (!name) return next()
        const file = resolve(dir, name)
        // Containment, not a prefix test on the parent: `..` in the url could otherwise walk out
        // of the registry and serve any file on the dev machine.
        if (!file.startsWith(dir + sep)) return next()
        if (!existsSync(file) || !statSync(file).isFile()) return next()
        res.setHeader('Cache-Control', 'no-store')
        if (name.endsWith('.json')) res.setHeader('Content-Type', 'application/json')
        res.end(readFileSync(file))
      })
    }
  }
}

/**
 * The PUBLIC marketplace page.
 *
 * Its own root (unlike agentd-web, which builds the shared renderer wholesale) because this is a
 * different application: no socket, no session, no store. It imports the card grid and the
 * stylesheet from ../ui so a listing looks identical in both places, and nothing else.
 *
 *   npm run dev -w agentd-marketplace     # :5274, reads AGENTD_REGISTRY
 *   npm run build -w agentd-marketplace   # static bundle -> marketplace/dist/
 *
 * The built output is uploaded to the marketplace site bucket as-is; `base: './'` keeps every
 * asset path relative so the same bundle works at a domain root, under a path prefix, or opened
 * off disk.
 */
export default defineConfig({
  // Pinned to this file's own folder rather than left to default to the cwd: the workspace scripts
  // run from here, but `vite --config marketplace/vite.config.ts` from the workspace root would
  // otherwise root at `clients/` and serve 404s for every path including the page itself.
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: './',
  plugins: [react(), devRegistry()],
  resolve: {
    alias: { '@ui': fileURLToPath(new URL('../ui/src', import.meta.url)) }
  },
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 5274 }
})
