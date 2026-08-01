import { useState } from 'react'

import { gateway } from '../gateway/client'
import { useAuthSession } from '../lib/auth'
import { setMode } from '../lib/mode'
import { useApp } from '../state/store'

/**
 * Startup launcher — the ComfyUI-style home that picks the desktop RUN MODE before the app opens.
 *
 *   Local card — BYOK: the daemon uses the user's own provider keys, no proxy, no metering.
 *   Cloud card — platform keys: sign in, and every model call is metered to the account via the
 *                Model Proxy. Its URL has a baked default (distribution profile) with an
 *                editable override here (persisted live via platform.setModelProxyUrl).
 *
 * Choosing a mode sets lib/mode (persisted) and re-asserts it on the live daemon (store.applyMode).
 * The choice is remembered; Settings ▸ "Switch mode" returns here.
 */
function LaptopIcon(): JSX.Element {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="3" y="4" width="18" height="12" rx="1.5" />
      <path d="M2 20h20M9 16h6" strokeLinecap="round" />
    </svg>
  )
}
function CloudIcon(): JSX.Element {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path
        d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17 9.5a3.5 3.5 0 0 1-.5 8.5H7z"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export default function Launcher(): JSX.Element {
  const hello = useApp((s) => s.hello)
  const flavor = useApp((s) => s.flavor)
  const applyMode = useApp((s) => s.applyMode)
  const session = useAuthSession()

  const proxy = hello?.platform?.modelProxy || hello?.platform?.modelGateway
  const proxyUrl = proxy?.api_base || ''
  const [editingUrl, setEditingUrl] = useState(false)
  const [urlDraft, setUrlDraft] = useState(proxyUrl)
  const [savingUrl, setSavingUrl] = useState(false)

  const product = flavor?.productName || 'agentd'

  async function chooseLocal(): Promise<void> {
    setMode('local')
    await applyMode()
  }

  function chooseCloud(): void {
    setMode('cloud')
    // App re-renders: if not signed in it shows the sign-in gate; once signed in, an effect
    // there calls applyMode() to connect. If already signed in, apply now.
    if (session) void applyMode()
  }

  async function saveUrl(): Promise<void> {
    setSavingUrl(true)
    try {
      await gateway.request('platform.setModelProxyUrl', { url: urlDraft.trim() })
      await applyMode() // refresh hello.platform.modelProxy
      setEditingUrl(false)
    } catch {
      /* older daemon / not connected — leave the editor open */
    } finally {
      setSavingUrl(false)
    }
  }

  return (
    <div className="launcher">
      <div className="launcher-brand">{product}</div>
      <div className="launcher-tagline">Choose how this app runs</div>

      <div className="launcher-cards">
        {/* -------- Local -------- */}
        <div className="launcher-card">
          <div className="launcher-card-icon">
            <LaptopIcon />
          </div>
          <div className="launcher-card-title">Local</div>
          <div className="launcher-card-sub">Bring your own API keys</div>
          <div className="launcher-card-body">
            Runs entirely on your machine. The agent calls model providers directly with the keys
            you set in Settings. No account, no metering.
          </div>
          <button className="launcher-btn" type="button" onClick={() => void chooseLocal()}>
            Open Local
          </button>
        </div>

        {/* -------- Cloud -------- */}
        <div className="launcher-card">
          <div className="launcher-card-icon">
            <CloudIcon />
          </div>
          <div className="launcher-card-title">Cloud</div>
          <div className="launcher-card-sub">Platform keys, metered to your account</div>
          <div className="launcher-card-body">
            Sign in and model calls route through the hosted Model Proxy on platform keys — nothing to
            configure, usage billed to your account.
          </div>

          <div className="launcher-gw">
            {editingUrl ? (
              <div className="launcher-gw-edit">
                <input
                  className="launcher-gw-input"
                  value={urlDraft}
                  placeholder="https://models.example.com"
                  onChange={(e) => setUrlDraft(e.target.value)}
                  spellCheck={false}
                  autoFocus
                />
                <button
                  className="launcher-gw-save"
                  type="button"
                  disabled={savingUrl}
                  onClick={() => void saveUrl()}
                >
                  {savingUrl ? '…' : 'Save'}
                </button>
              </div>
            ) : (
              <button
                className="launcher-gw-show"
                type="button"
                title="Override the Model Proxy URL"
                onClick={() => {
                  setUrlDraft(proxyUrl)
                  setEditingUrl(true)
                }}
              >
                <span className="launcher-gw-label">Model Proxy</span>
                <span className="launcher-gw-url">{proxyUrl || 'default (not set) — click to set'}</span>
              </button>
            )}
          </div>

          <button className="launcher-btn" type="button" onClick={chooseCloud}>
            {session ? 'Open Cloud' : 'Sign in & use Cloud'}
          </button>
        </div>
      </div>
    </div>
  )
}
