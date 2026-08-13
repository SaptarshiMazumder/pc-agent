/**
 * Desktop run mode — Local vs Cloud.
 *
 *   local  — BYOK. The daemon calls model providers directly with the user's own keys
 *            (Settings ▸ API Keys). No proxy, no metering.
 *   cloud  — Platform keys. The connection's session token pays; every call is metered.
 *
 * The mode is A PROPERTY OF THE CONNECTION: the store's url provider appends `?mode=` when the
 * socket dials, and the daemon pins it per connection (two windows can differ). Which is why
 * `setMode` MUST rebuild the socket — the choice only exists on the wire. Before it did, picking
 * Cloud in the launcher changed the UI and changed nothing else; there used to be a
 * `platform.connect` call that applied the mode daemon-side, but that API now requires a token
 * and the whole idea of the daemon holding the mode is gone with the design that had it.
 *
 * Persisted, so a returning user lands straight in their last mode. `null` => no choice yet =>
 * show the launcher. Desktop-only (web ignores it).
 */

import { useSyncExternalStore } from 'react'

import { gateway } from '../gateway/client'

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
  const changed = mode !== cached
  cached = mode
  try {
    if (mode) localStorage.setItem(LS_KEY, mode)
    else localStorage.removeItem(LS_KEY)
  } catch {
    /* private mode / quota — the in-memory cache still drives this session */
  }
  listeners.forEach((l) => l())
  // The wire is where the mode lives (`?mode=` on the connect url) — rebuild the socket so the
  // choice takes effect NOW, not on whichever future reconnect happens to come along.
  if (changed && mode !== null) gateway.reconnect()
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
