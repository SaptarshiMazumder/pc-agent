/* The preview pane — the agent's own window running live beside the conversation building it.
 *
 * WHAT THIS REPLACES: the loop where seeing a change meant build → switch windows → find the
 * agent → open → come back. The window now runs in the middle of the workspace, on the recessed
 * stage, framed like a device.
 *
 * THE IFRAME IS THE REAL APP, on the same launch URL `Open app` builds — scope, daemon token,
 * access token, mode — so what runs here is exactly what a separate window would run, signed in
 * as the same person. The URL is awaited (the access token renews first), so the frame mounts
 * signed in rather than anonymous.
 *
 * FRESHNESS COMES FROM THE DAEMON, not from this pane. The daemon rebuilds `ui/` whenever a
 * tool writes into `app/` (its build-on-write observer) and broadcasts `app.rebuilt`; the agent's
 * own window — this iframe included — carries the LiveReload listener every scaffolded app ships,
 * and reloads itself. So the pane shows current source without polling anything. The Rebuild
 * button remains for the one case that misses: source edited by something that is not a tool
 * (an editor on disk), where nothing fired the observer.
 *
 * A FAILED REBUILD SHOWS VITE'S ERROR AND KEEPS THE OLD FRAME VISIBLY STALE — the same honesty
 * rule as buildAndOpen: pretending a broken build succeeded is the worst outcome available.
 */

import { ExternalLink, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { appLaunchUrl, buildApp, hasWindow, openAgentWindow } from '../agentd/app-window'
import type { AgentRow } from '../agentd/roster'

export function WorkspacePreview({
  client,
  agent,
  /** Does this agent COMPILE its window (an `app/` dir at its root)? The caller knows from the
   *  file tree — same fact the Open-app button reads. A hand-written ui/ has nothing to build. */
  compiles,
}: {
  client: { invokeTool(name: string, params: Record<string, unknown>): Promise<unknown> }
  agent: AgentRow
  compiles: boolean
}) {
  const [url, setUrl] = useState('')
  const [urlError, setUrlError] = useState('')
  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState('')
  const [opening, setOpening] = useState(false)
  /** Bumped to remount the iframe after a MANUAL rebuild — the daemon-driven path reloads
   *  itself via the app's own LiveReload and needs nothing from us. */
  const [nonce, setNonce] = useState(0)
  const frame = useRef<HTMLIFrameElement>(null)

  /* THE WINDOW IS RENDERED AT DESKTOP WIDTH AND SCALED, never squeezed. A fluid iframe below
   * the template's minimum lets the template's own responsive rules collapse it — the pane then
   * shows a phone layout (or worse, just its rail) and LIES about how the agent looks. Same
   * idiom as every template thumbnail in this app: fixed 1280 design width, transform-scaled to
   * whatever the pane affords, remeasured live. */
  const fit = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ w: number; h: number }>({ w: 0, h: 0 })
  useEffect(() => {
    const el = fit.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [url])
  const DESIGN_W = 1280
  const scale = box.w > 0 ? Math.min(1, box.w / DESIGN_W) : 0

  useEffect(() => {
    let live = true
    setUrl('')
    setUrlError('')
    void appLaunchUrl(agent)
      .then((u) => {
        if (live) setUrl(u)
      })
      .catch((e) => {
        if (live) setUrlError(String((e as Error)?.message || e))
      })
    return () => {
      live = false
    }
  }, [agent])

  const rebuild = async (): Promise<void> => {
    setBuilding(true)
    setBuildError('')
    try {
      if (compiles) await buildApp(client, agent)
      setNonce((n) => n + 1)
    } catch (e) {
      // The old frame stays — visibly stale beside the error, never silently current.
      setBuildError(String((e as Error)?.message || e))
    } finally {
      setBuilding(false)
    }
  }

  const openOut = async (): Promise<void> => {
    setOpening(true)
    setBuildError('')
    try {
      await openAgentWindow(agent)
    } catch (e) {
      setBuildError(String((e as Error)?.message || e))
    } finally {
      setOpening(false)
    }
  }

  if (!hasWindow(agent)) return null

  return (
    <div className="wsp">
      <div className="wsp-bar">
        <code className="wsp-url">{agent.app?.url || ''}</code>
        <button
          className="icon-btn icon-btn--sm"
          onClick={() => void rebuild()}
          disabled={building}
          title={
            building
              ? 'Rebuilding…'
              : compiles
                ? 'Rebuild from app/ and reload'
                : 'Reload (hand-written ui/ — nothing to build)'
          }
          aria-label="Rebuild the preview"
        >
          <RefreshCw size={15} className={building ? 'spin' : undefined} />
        </button>
        <button
          className="icon-btn icon-btn--sm"
          onClick={() => void openOut()}
          disabled={opening}
          title={opening ? 'Opening…' : 'Open in its own window'}
          aria-label="Open in its own window"
        >
          <ExternalLink size={15} />
        </button>
      </div>

      {buildError && (
        <div className="wsp-error" role="alert">
          {buildError}
        </div>
      )}

      <div className="wsp-stage">
        {url ? (
          <div ref={fit} className="wsp-fit">
            {scale > 0 && (
              <iframe
                key={nonce}
                ref={frame}
                className="wsp-frame"
                src={url}
                title={`${agent.name || agent.id} — live preview`}
                style={{
                  width: DESIGN_W,
                  height: Math.round(box.h / scale),
                  transform: `scale(${scale})`,
                }}
              />
            )}
          </div>
        ) : (
          <div className="wsp-empty">{urlError || 'preparing the window…'}</div>
        )}
      </div>
    </div>
  )
}
