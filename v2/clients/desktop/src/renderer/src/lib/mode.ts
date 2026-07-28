/**
 * Desktop run mode — Local vs Cloud.
 *
 *   local  — BYOK. The daemon calls model providers directly with the user's own keys
 *            (Settings ▸ API Keys). No gateway, no metering. platform.disconnect.
 *   cloud  — Platform keys. Sign in; the session token becomes the model-proxy credential
 *            (platform.connect) and every model call is metered to the account.
 *
 * The ComfyUI-style launcher (Launcher.tsx) sets this; store.ts's connectPlatform reads it to
 * connect/disconnect on every handshake. Persisted, so a returning user lands straight in their
 * last mode. `null` => no choice yet => show the launcher. Desktop-only (web ignores it).
 */

import { useSyncExternalStore } from 'react'

export type RunMode = 'local' | 'cloud'

const LS_KEY = 'agentd.mode'
const listeners = new Set<() => void>()

function readLS(): RunMode | null {
  try {
    const v = localStorage.getItem(LS_KEY)
    return v === 'local' || v === 'cloud' ? v : null
  } catch {
    return null
  }
}

// cached snapshot so useSyncExternalStore sees a STABLE reference between changes
let cached: RunMode | null = readLS()

export function getMode(): RunMode | null {
  return cached
}

/** Set (or clear, with null) the run mode and notify subscribers. Clearing returns to the launcher. */
export function setMode(mode: RunMode | null): void {
  cached = mode
  try {
    if (mode) localStorage.setItem(LS_KEY, mode)
    else localStorage.removeItem(LS_KEY)
  } catch {
    /* private mode / quota — the in-memory cache still drives this session */
  }
  listeners.forEach((l) => l())
}

/** React hook: the current run mode (re-renders on change). */
export function useMode(): RunMode | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    getMode,
    getMode
  )
}
