// Minimal browser globals so the renderer modules run under node. See auth-smoke.ts.
const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k)
}
globalThis.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
globalThis.location = { search: '', hostname: '127.0.0.1', protocol: 'http:', host: '127.0.0.1', origin: 'http://127.0.0.1' }
Object.defineProperty(globalThis, 'navigator', { value: { userAgent: 'node', platform: 'win32' }, configurable: true })
globalThis.document = { documentElement: { dataset: {} } }
await import('./auth-smoke.bundle.mjs')
