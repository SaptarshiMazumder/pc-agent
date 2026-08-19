/**
 * localStorage-backed stores, and the KEY each host reads.
 *
 * The key is a parameter rather than a constant for one reason: two agent windows served from the
 * same origin that shared a key would silently become one session — one agent's credential
 * quietly becoming another's.
 */

import type { SessionStore } from './types'

/** A `SessionStore` over localStorage, inert where storage is unavailable. */
export function localSessionStore(key: string): SessionStore {
  return {
    read() {
      try {
        return localStorage.getItem(key)
      } catch {
        return null // private mode: this run still works, it just will not persist
      }
    },
    write(value) {
      try {
        if (value === null) localStorage.removeItem(key)
        else localStorage.setItem(key, value)
      } catch {
        /* non-fatal, same reason */
      }
    }
  }
}

/** An in-memory store — for tests, and for a host that must not persist at all. */
export function memorySessionStore(): SessionStore {
  let held: string | null = null
  return {
    read: () => held,
    write: (value) => {
      held = value
    }
  }
}
