import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Static marketing site. Emits a fully self-contained bundle in dist/ —
// no runtime API calls, no external font/asset hosts.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
  },
})
