import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

/**
 * The renderer is NOT in this package: it is the shared `@agentd/ui` workspace (../ui), the very
 * same sources web/vite.config.ts builds for the browser client. Pointing `root` at it is what
 * keeps ONE UI serving both shells — no fork, no copy. Everything Electron-specific stays here
 * (src/main, src/preload); the UI reaches host capabilities only via ui/src/lib/platform.ts.
 */
export default defineConfig({
  main: { plugins: [externalizeDepsPlugin()] },
  // TWO preloads: the shell's full bridge, and a receive-only one for AGENT APP windows.
  // They are separate builds because they are separate trust levels — see src/preload/app.ts.
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: fileURLToPath(new URL('src/preload/index.ts', import.meta.url)),
          app: fileURLToPath(new URL('src/preload/app.ts', import.meta.url))
        }
      }
    }
  },
  renderer: {
    root: fileURLToPath(new URL('../ui', import.meta.url)),
    plugins: [react()],
    build: {
      rollupOptions: { input: fileURLToPath(new URL('../ui/index.html', import.meta.url)) }
    }
  }
})
