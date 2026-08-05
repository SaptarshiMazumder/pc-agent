/**
 * Is there money to show? — the one place that question is answered.
 *
 * Four surfaces need it (the balance chip in the composer, the Account page's Credits block, the
 * menu label, and the billing page itself), and getting it wrong in either direction is bad: show
 * a balance to a BYOK user and it implies their own API key is being billed by us; hide it from a
 * metered user and they cannot find out why their next message was refused.
 *
 * THE TEST IS THE SESSION PLUS THE RUN MODE, NOT THE DAEMON'S PROXY STATUS. `hello.platform
 * .modelProxy.enabled` is a fact about the MACHINE — whether this daemon routes through the
 * proxy — and on the hosted web client, where the cloud daemon holds the master key itself, it can
 * be false for a user who is unambiguously on a metered account. That mismatch is what made the
 * Credits block disappear on web.
 *
 * `metered`: the platform is paying for inference, so credits are the currency. Always true on the
 *   web (there is no BYOK web build) and true on desktop only in Cloud mode.
 * `billing`: metered AND signed in — there is an actual account with an actual balance.
 */

import { useAuthSession } from './auth'
import { useMode } from './mode'
import { isDesktop } from './platform'

export function useBilling(): { metered: boolean; billing: boolean } {
  const session = useAuthSession()
  const mode = useMode()
  const metered = !isDesktop || mode === 'cloud'
  return { metered, billing: !!session && metered }
}
