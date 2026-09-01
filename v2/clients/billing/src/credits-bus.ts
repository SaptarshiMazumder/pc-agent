/**
 * "The balance probably changed" — one tiny bus, so nothing polls a money endpoint on a timer.
 *
 * A balance moves without the thing showing it doing anything that would re-render: a purchase on
 * the credits panel has to move the chip in the composer, and a message that spends credits has to
 * move both. The alternative is every consumer polling, which is a cost paid forever for an event
 * that is rare.
 *
 * Module-level on purpose. There is one balance per signed-in account per window, so a per-client
 * bus would just be the same set with more wiring.
 */

const listeners = new Set<() => void>()

/** Subscribe to "the balance probably changed"; returns an unsubscribe. */
export function onCreditsChanged(cb: () => void): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

/** Announce a balance change. Called after any purchase; safe to call after any known debit. */
export function notifyCreditsChanged(): void {
  listeners.forEach((l) => l())
}
