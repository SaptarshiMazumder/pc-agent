/* Run mode — Local (your own API keys) vs Cloud (platform keys, metered to your account).
 *
 * MACHINE-WIDE, AND IT SAYS SO. The model proxy is one piece of daemon state shared by every
 * agent, so this control flips the others too. A toggle that silently changed every other agent on
 * the machine would be the worst kind of surprise.
 *
 * The daemon is the source of truth. This used to live in the agentd desktop app's own
 * localStorage, which no other page could read — which is exactly why changing mode meant leaving
 * the agent, opening agentd, and switching there.
 *
 * Default is Cloud: signing in puts a daemon with no stated preference onto platform keys with
 * nothing pressed. Choosing Local is remembered, so the next sign-in does not undo it.
 */

import type { AuthState, RunMode } from '@agentd/client'

export function ModeSection({
  auth,
  chosen,
  error,
  onSwitch,
}: {
  auth: AuthState | null
  chosen: RunMode | ''
  error: string
  onSwitch: (next: RunMode) => void
}) {
  if (error) {
    return (
      <section className="set-group">
        <h2>Run mode</h2>
        <div className="loading">could not read the run mode: {error}</div>
      </section>
    )
  }

  const cloud = auth?.mode === 'cloud'
  const canCloud = !!auth?.canUseCloud

  return (
    <section className="set-group">
      <h2>Run mode</h2>
      <p className="ghelp">
        Who pays for model calls. This applies to EVERY agent on this machine, not just this one.
      </p>
      <div className="field">
        <div>
          <label>{cloud ? 'Cloud — platform keys' : 'Local — your own API keys'}</label>
          <span className="fhelp">
            {cloud
              ? 'Model calls route through the hosted proxy and are metered to your account.'
              : 'Model calls go straight to the providers, with the keys set below.'}
          </span>
          {/* "Cloud" and "Cloud because nobody said otherwise" are different states, and only the
              second one changes by itself when someone signs in. */}
          {!chosen && <span className="fhelp">default — you have not chosen yet</span>}
          {!cloud && !canCloud && (
            <span className="fhelp">
              {auth?.available
                ? 'Sign in to use Cloud.'
                : 'This build has no model proxy, so Cloud is unavailable.'}
            </span>
          )}
        </div>
        <button
          className={`toggle ${cloud ? 'on' : ''}`}
          disabled={!cloud && !canCloud}
          onClick={() => onSwitch(cloud ? 'local' : 'cloud')}
        />
      </div>
    </section>
  )
}
