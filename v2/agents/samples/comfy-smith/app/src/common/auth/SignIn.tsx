/* signInFirst — sign the user in, THEN render the app.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source.
 *
 * Call it from `main.tsx` and await it before the first render:
 *
 *     await signInFirst('My Agent')
 *     root.render(<App />)
 *
 * WHY BLOCKING. An app that renders its composer first and signs in later has to answer "signed in
 * yet?" at every send site, and gets it wrong at one of them. Past this line somebody is signed in
 * — or this build has no accounts service, in which case nothing was ever asked.
 *
 * WHY THE SDK'S GATE AND NEVER YOUR OWN FORM. Its element ids (`gateEmail`, `gatePass`, `gateForm`)
 * are a contract the packaged-build login test drives, so a hand-written login silently disables
 * that test. It hits the same endpoints either way; the only thing a custom form adds is a way to
 * get credentials wrong. Theme it with the `--gate-*` CSS custom properties.
 *
 * IT RENDERS NOTHING when this build has no accounts service, when the window already carries a
 * credential, or when a stored session still works — so it is safe to call unconditionally, and
 * there is no branch for you to test.
 */

import { mountSignInGate } from '@agentd/client'

export async function signInFirst(product = ''): Promise<void> {
  try {
    await mountSignInGate({
      // Falls back to the page title, so an agent that passes nothing still names itself.
      product: product || undefined,
      blurb: 'Sign in to continue.',
    })
  } catch (e) {
    // The accounts service being unreachable must NOT leave a blank window. Render the app —
    // everything that does not need an account still works, and the account controls report the
    // real state — rather than trapping the user behind a form that cannot succeed.
    console.error('[sign-in]', e)
  }
}
