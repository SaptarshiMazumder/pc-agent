/* One button: open this account's ComfyUI in a new tab.
 *
 * NO TOOL, NO DAEMON, NO SANDBOX. The window asks the platform directly — the same accounts
 * service, the same sign-in token and the same "about my own account" rule the credits number
 * beside it already uses — for the machine's openable link, and opens it. It used to depend on
 * the window's own `gpu_ensure` poll having succeeded through the microVM, which is a call that
 * can fail before it reaches anything and then says nothing; this asks fresh, every click, and
 * says exactly what it got.
 *
 * THE TAB OPENS ON THE CLICK, before the answer arrives. A tab opened after an await is a popup
 * to the browser and gets blocked; one opened synchronously and pointed somewhere later is fine.
 * If the platform has no machine, the empty tab is closed again and the reason sits beside the
 * button.
 *
 * The link carries the machine's login token, so the tab lands in ComfyUI itself — not on the
 * portal's password page nobody has the password for.
 */

import { accessTokenAccount, creditsHost, type AgentdClient } from '@agentd/client'
import { ExternalLink, Loader2 } from 'lucide-react'
import { useState } from 'react'

/** The platform's answer for this account's machine, as the signed-in person. */
async function openableLink(client: AgentdClient): Promise<string> {
  const host = creditsHost({ client })
  const [base, token] = await Promise.all([host.accountsUrl(), host.accessToken()])
  const origin = String(base || '').replace(/\/$/, '')
  if (!origin) throw new Error('this daemon has no accounts service')
  if (!token) throw new Error('sign in first')
  const account = accessTokenAccount(String(token))
  if (!account) throw new Error('sign in first')
  const r = await fetch(`${origin}/vast/status/${encodeURIComponent(account)}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!r.ok) throw new Error(`the platform answered ${r.status}`)
  const d = (await r.json()) as { ready?: boolean; open_url?: string; state?: string }
  if (!d.ready || !d.open_url) {
    throw new Error(d.state === 'starting' ? 'the GPU is still starting' : 'no GPU is running')
  }
  return d.open_url
}

/** Where the engine is, as far as the window knows — decides whether the button can open. */
export interface EngineReadiness {
  ready: boolean
  /** What to say when it is not ready: "Engine starting…", "Engine offline". */
  label: string
  /** A boot or a check is in progress, so the label spins rather than sits. */
  pending: boolean
}

/* THE LOUDEST THING IN THE STAGE'S BAR WHEN THERE IS SOMETHING TO OPEN (creative-studio
   redesign): a filled accent button once the engine answers, because watching the queue in
   ComfyUI itself is the one thing a person may want to do outside this window. Before that it is
   the same button, disabled, saying why — starting, waiting for a GPU, offline — so the control
   never moves and never lies about being clickable. */
export function OpenComfyButton({ client, engine }: { client?: AgentdClient; engine: EngineReadiness }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const open = () => {
    if (!client || busy) return
    const tab = window.open('', '_blank')
    setBusy(true)
    setNote('')
    void openableLink(client)
      .then((url) => {
        if (tab) tab.location.href = url
        else window.location.assign(url)
      })
      .catch((e) => {
        tab?.close()
        setNote(String((e as Error)?.message || e))
      })
      .finally(() => setBusy(false))
  }

  const live = engine.ready && !!client
  return (
    <>
      <button
        className={`eng-open${live ? ' is-live' : ''}`}
        onClick={open}
        disabled={!live || busy}
        title={live ? "Open this account's ComfyUI in a new tab" : engine.label}
      >
        {live ? (
          <>
            <span className="eng-dot" aria-hidden="true" />
            {busy ? 'Opening…' : 'Open in ComfyUI'}
            <ExternalLink size={13} strokeWidth={2.2} aria-hidden="true" />
          </>
        ) : (
          <>
            {engine.pending ? (
              <Loader2 size={13} strokeWidth={2.2} className="spin" aria-hidden="true" />
            ) : (
              <span className="eng-dot is-off" aria-hidden="true" />
            )}
            {engine.label}
          </>
        )}
      </button>
      {note && <span className="sb-open-note">{note}</span>}
    </>
  )
}
