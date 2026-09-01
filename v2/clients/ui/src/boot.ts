/**
 * BOOT — what every entry point must settle before the first frame.
 *
 * There are two documents now: the app (index.html) and the admin console (admin.html). Both need
 * the same three answers before anything renders, and getting them in a different order — or
 * skipping one — is how an entry point comes to behave subtly unlike its sibling. So the sequence
 * lives here once, and an entry is a call to it plus a render.
 *
 * WHY IT IS AWAITED, at the cost of a round trip before first paint: the platform document decides
 * whether this build even HAS sign-in. Rendering before the answer arrives means showing the
 * console to someone who is then bounced to a login screen a moment later. Every step resolves to
 * a no-op on failure (offline, no flavor, BYOK build), so this can delay startup but never
 * prevent it.
 */

import { configureAccounts, restoreSession } from './lib/auth'
import { configurePlatform, discoverPlatform } from './lib/discovery'
import { platform } from './lib/platform'
import { initialTheme } from './state/store'

/** Paint the persisted theme onto <html> before the first frame, so there is no flash of the
 *  wrong one. Called at module scope by each entry, not inside the async boot below — a theme
 *  applied after an await is a theme the user watches change. */
export function applyStoredTheme(): void {
  document.documentElement.dataset.theme = initialTheme()
}

/**
 * Discover where this build's platform lives, then restore any stored session.
 *
 * TWO SOURCES, ONE PREFERRED. `platformUrl` is the single address a modern build bakes, and
 * everything else (accounts, model proxy, ws, providers) is fetched from it. `accountsUrl` is what
 * older flavors carry and stays as the fallback, so an installer shipped before discovery existed
 * keeps working exactly as it did.
 *
 * "Stay signed in" is a refresh token and nothing else durable, so the exchange here turns it into
 * a usable pair before the first frame — otherwise a returning admin sees the sign-in gate for a
 * moment and gets bounced past it.
 */
export async function bootPlatform(): Promise<void> {
  try {
    const flavor = (await platform.flavor()) as { accountsUrl?: string; platformUrl?: string }
    configurePlatform(String(flavor.platformUrl || ''))
    configureAccounts(String(flavor.accountsUrl || ''))
    await discoverPlatform()
    await restoreSession()
  } catch {
    /* no flavor (or bridge hiccup) => BYOK behavior, same as before */
  }
}
