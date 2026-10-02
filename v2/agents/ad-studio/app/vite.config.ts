import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

// Three settings here are load-bearing. Change any of them and the app builds fine and then
// fails on a real install, which is the worst kind of wrong.
export default defineConfig({
  plugins: [react()],

  // RELATIVE ASSET URLS. The app is served under /apps/<agent-id>/, so an absolute "/assets/…"
  // asks the daemon ROOT for a file that is not there and every chunk 404s — a blank window with
  // a clean console.
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

  build: {
    // ui/ IS WHAT SHIPS. app/ is source and never leaves the author's machine; agent.toml points
    // [app] entry at ui/index.html, and the packer takes what is on disk. Nobody installing this
    // agent runs npm.
    outDir: '../ui',
    emptyOutDir: true,
  },
})
