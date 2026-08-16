import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Served by the daemon out of `agents/<id>/ui/` at `/apps/<id>/`. Three settings follow from
// that, and none of them is a style choice:
//
//   base: './'      assets resolve RELATIVE to the page. An absolute '/assets/…' would ask the
//                   daemon's root instead of this agent's folder, and every chunk 404s.
//   outDir: '../ui' the BUILT output is what ships — `ui/` is packaged into the .agentpkg and
//                   served as-is. `app/` is the source, kept beside it so the next author can
//                   rebuild. Nobody installing this agent ever runs npm.
//   emptyOutDir     otherwise stale hashed chunks from an older build pile up in what we ship.
export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: {
      // THE SDK LIVES INSIDE THIS APP, on purpose.
      //
      // The obvious alternative is a dependency: "@agentd/client": "file:../../../clients/sdk-js".
      // That works only inside the product's own repo. An agent installed on someone else's
      // machine has no such path, `npm install` fails, and the app cannot be built at all — by
      // whoever received the agent, which is everyone except its author.
      //
      // Vendored, it is a plain file in the tree. No registry, no relative escape, no install
      // step that can fail. The SDK's own build refreshes this copy (clients/sdk-js/scripts/
      // vendor.mjs), so it tracks the daemon it talks to.
      '@agentd/client': fileURLToPath(new URL('./vendor/agentd-client.js', import.meta.url)),
    },
  },
  build: { outDir: '../ui', emptyOutDir: true },
})
