/* Ship — preflight, version, audience and the two real verbs, as a screen instead of three
 * buttons and a window.confirm.
 *
 * THE VERBS ARE THE INSPECTOR'S, SEMANTICS INTACT. Validate is the same validate_agent call.
 * Publish keeps the exact two-step contract: the first call is the tool's default DRY RUN,
 * printing the index that would be published; only an explicit yes sends dry_run=false +
 * confirm=true (the tool requires both, so nothing here can publish by accident either); a
 * failed dry run stops rather than asking anyone to confirm something that cannot work; cancel
 * reports "cancelled — nothing was uploaded". What changed is only WHERE the confirmation
 * happens: the dry-run output renders on the page with Confirm/Cancel beside it, instead of a
 * window.confirm the preview hid behind. Download is package_agent, verbatim.
 *
 * WHAT IS DRAWN BUT INERT, each for a missing mechanism: the version tiles (publish_agent takes
 * no version — bumps happen in the conversation, where the agent edits its own agent.toml), the
 * audience tiles (no such parameter — the daemon publishes to the registry it is configured
 * for), the changelog (nothing consumes it), the listing's Install button (this machine already
 * has the agent), and the bundle list (its contents exist only inside a package run). Every one
 * says so in its title rather than pretending.
 *
 * PREFLIGHT RUNS WHAT IS SAFE UNPROMPTED and asks before what is not: validation runs on open
 * (read-only, the first thing anyone shipping wants to know); the build row offers a button
 * (a build writes ui/). Test-driven is dim truth: nothing records test runs yet.
 */

import {
  ArrowUp,
  Check,
  ChevronLeft,
  CircleDashed,
  Download,
  Play,
  Shield,
  X,
  XCircle,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { AgentdClient } from '@agentd/client'
import { resultText } from '../agentd/chat'
import { hasWindow } from '../agentd/app-window'
import { publishable, publishBlockReason, type AgentRow } from '../agentd/roster'
import { useAuthorship } from '../lib/authorship'
import { agentColor, agentInitials } from '../lib/agentPresentation'

type RowState = { state: 'idle' | 'running' | 'ok' | 'bad' | 'off'; text: string }

export function ShipScreen({
  client,
  agent,
  authorLabel,
  onClose,
}: {
  client: AgentdClient
  agent: AgentRow
  /** The identity a publish would be signed with — the topbar's whoami, passed through. */
  authorLabel: string
  onClose: () => void
}) {
  const [validation, setValidation] = useState<RowState>({ state: 'running', text: 'running validate_agent…' })
  const [build, setBuild] = useState<RowState>({ state: 'idle', text: 'not built from here yet' })
  const [showReport, setShowReport] = useState(false)
  /** The publish machine: idle → previewing (dry run) → confirm (diff on screen) → publishing →
   *  a final report. Mirrors the inspector's flow with the confirm rendered instead of popped. */
  const [pub, setPub] = useState<
    | { step: 'idle' }
    | { step: 'previewing' }
    | { step: 'confirm'; preview: string }
    | { step: 'publishing'; preview: string }
    | { step: 'done'; text: string; bad: boolean }
  >({ step: 'idle' })
  const [packing, setPacking] = useState(false)
  const [packOut, setPackOut] = useState<{ text: string; bad: boolean } | null>(null)

  // WHERE IT GOES. Defaulted from membership once the answer is known, never guessed before —
  // defaulting to "marketplace" while the org lookup is still in flight is how an enterprise's
  // internal agent gets aimed at a public listing.
  const authorship = useAuthorship()
  const [audience, setAudience] = useState<'org' | 'marketplace'>('marketplace')
  const chosen = useRef(false)
  useEffect(() => {
    if (!authorship.resolved || chosen.current) return
    chosen.current = true // a later re-resolve must not overwrite what the user picked
    setAudience(authorship.enterprise ? 'org' : 'marketplace')
  }, [authorship.resolved, authorship.enterprise])
  const orgName = authorship.orgs[0]?.name || 'your organization'

  const canPublish = publishable(agent)

  const invoke = async (tool: string, extra: Record<string, unknown> = {}): Promise<string> =>
    resultText(await client.invokeTool(tool, { agent_id: agent.id, ...extra })) || '(no output)'

  // Validation on open — read-only, and the first fact a shipping screen owes its user.
  const ran = useRef(false)
  useEffect(() => {
    if (ran.current) return
    ran.current = true
    void (async () => {
      try {
        const text = await invoke('validate_agent')
        const bad = /\[x\]/i.test(text)
        setValidation({ state: bad ? 'bad' : 'ok', text })
      } catch (e) {
        setValidation({ state: 'bad', text: String((e as Error)?.message || e) })
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const runBuild = async (): Promise<void> => {
    setBuild({ state: 'running', text: 'building…' })
    try {
      const text = await invoke('build_app')
      setBuild({ state: 'ok', text })
    } catch (e) {
      setBuild({ state: 'bad', text: String((e as Error)?.message || e) })
    }
  }

  /* SHIPPING TO THE ORG IS ONE STEP, deliberately.

     It is the SAME pipeline as a marketplace publish -- packed, signed with the author's creator
     key, versioned, installer built -- against the organization's own private registry instead of
     the public one. So a colleague on any machine installs it exactly as they would a marketplace
     agent, and a version supersedes or rolls back like any other.

     What it does NOT have is the review. The dry-run/confirm pair guards a PUBLIC listing that
     strangers install and that the platform's roster vouches for; reaching your own colleagues is
     neither, and the company already vouched for its own staff by employing them. Making an
     enterprise walk a review queue to reach its own people is the friction this path exists to
     delete. */
  const shipToOrg = async (): Promise<void> => {
    setPub({ step: 'publishing', preview: '' })
    try {
      const text = await invoke('publish_agent', { destination: 'org' })
      setPub({ step: 'done', text, bad: false })
    } catch (e) {
      setPub({ step: 'done', text: String((e as Error)?.message || e), bad: true })
    }
  }

  const startPublish = async (): Promise<void> => {
    setPub({ step: 'previewing' })
    try {
      const preview = await invoke('publish_agent', { destination: 'marketplace', dry_run: true })
      setPub({ step: 'confirm', preview })
    } catch (e) {
      // A dry run that failed — usually "not configured to publish" — ends the flow; asking
      // someone to confirm something that cannot work is the inspector's rule too.
      setPub({ step: 'done', text: String((e as Error)?.message || e), bad: true })
    }
  }

  const confirmPublish = async (preview: string): Promise<void> => {
    setPub({ step: 'publishing', preview })
    try {
      const text = await invoke('publish_agent', {
        destination: 'marketplace',
        dry_run: false,
        confirm: true,
      })
      setPub({ step: 'done', text, bad: false })
    } catch (e) {
      setPub({ step: 'done', text: String((e as Error)?.message || e), bad: true })
    }
  }

  const runPackage = async (): Promise<void> => {
    setPacking(true)
    setPackOut(null)
    try {
      setPackOut({ text: await invoke('package_agent'), bad: false })
    } catch (e) {
      setPackOut({ text: String((e as Error)?.message || e), bad: true })
    } finally {
      setPacking(false)
    }
  }

  const rowIcon = (s: RowState['state']) =>
    s === 'ok' ? (
      <Check size={15} className="ship-ok" />
    ) : s === 'bad' ? (
      <XCircle size={15} className="ship-bad" />
    ) : s === 'running' ? (
      <span className="ship-spinner" />
    ) : (
      <CircleDashed size={15} className="ship-dim" />
    )

  return (
    <div className="ship" role="dialog" aria-modal="true" aria-label={`Ship ${agent.name || agent.id}`}>
      <header className="bp-head">
        <button className="icon-btn" onClick={onClose} title="Back">
          <ChevronLeft size={17} />
        </button>
        <span className="subj-avatar" style={{ background: agentColor(agent.color, agent.id) }}>
          {agentInitials(agent.name, agent.id)}
        </span>
        <h2 className="bp-head-title">{agent.name || agent.id}</h2>
        <button className="icon-btn push-end" onClick={onClose} title="Close (Esc)">
          <X size={16} />
        </button>
      </header>

      <div className="ship-body">
        <div className="ship-main">
          <h2 className="lp-title">Ship it</h2>

          <div className="ship-rows">
            <div className={`ship-row ${validation.state === 'bad' ? 'is-bad' : ''}`}>
              {rowIcon(validation.state)}
              <span className="ship-row-name">Validation</span>
              <span className="ship-row-note">
                {validation.state === 'running'
                  ? 'running…'
                  : validation.state === 'ok'
                    ? 'clean'
                    : 'findings — read the report'}
              </span>
              <button className="link-btn" onClick={() => setShowReport((v) => !v)}>
                {showReport ? 'hide report' : 'report'}
              </button>
            </div>
            {showReport && <pre className="ship-report">{validation.text}</pre>}

            {hasWindow(agent) && (
              <div className={`ship-row ${build.state === 'bad' ? 'is-bad' : ''}`}>
                {rowIcon(build.state)}
                <span className="ship-row-name">Window builds</span>
                <span className="ship-row-note">{build.state === 'ok' ? 'built' : build.text.split('\n')[0]}</span>
                <button className="link-btn" disabled={build.state === 'running'} onClick={() => void runBuild()}>
                  {build.state === 'running' ? 'building…' : 'build now'}
                </button>
              </div>
            )}
            {build.state === 'bad' && <pre className="ship-report">{build.text}</pre>}

            <div className="ship-row">
              <CircleDashed size={15} className="ship-dim" />
              <span className="ship-row-name">Test-driven</span>
              <span className="ship-row-note">not tracked yet — nothing records test runs</span>
            </div>
          </div>

          <div className="ship-twocol">
            <div className="bp-field">
              <span className="bp-label">Version</span>
              <div className="ship-tiles">
                <span className="ship-tile is-current" title="The version in agent.toml">
                  v{agent.version || '—'}
                  <small>current</small>
                </span>
                <span className="ship-tile" aria-disabled="true" title="Not wired — version bumps happen in the conversation">
                  bump
                  <small>in the chat</small>
                </span>
              </div>
            </div>
            <div className="bp-field">
              <span className="bp-label">Audience</span>
              {/* WIRED, and it decides which pipeline runs. "My org" hands the agent to the
                  caller's colleagues directly — no public listing, no review; "Marketplace" is the
                  public path with its dry-run + confirm. Defaulted to the org for anyone who
                  belongs to one, because that is what an enterprise means by "publish". */}
              <div className="ship-tiles">
                <button
                  type="button"
                  className={`ship-tile ${audience === 'org' ? 'is-current' : ''}`}
                  disabled={!authorship.enterprise}
                  title={
                    authorship.enterprise
                      ? `Ship straight to ${orgName} — every member can install it, on any machine, with no review`
                      : 'You are not in an organization'
                  }
                  onClick={() => setAudience('org')}
                >
                  My org
                  <small>{authorship.enterprise ? 'no review' : 'not a member'}</small>
                </button>
                <button
                  type="button"
                  className={`ship-tile ${audience === 'marketplace' ? 'is-current' : ''}`}
                  title="Publish a PUBLIC listing — your first one files for review"
                  onClick={() => setAudience('marketplace')}
                >
                  Marketplace
                  <small>public</small>
                </button>
              </div>
            </div>
          </div>

          <div className="ship-actions">
            {pub.step === 'idle' || pub.step === 'previewing' ? (
              <button
                className="prime-btn"
                disabled={!canPublish || pub.step === 'previewing'}
                title={
                  !canPublish
                    ? publishBlockReason(agent)
                    : audience === 'org'
                      ? `Ship it to ${orgName} — every member can install it straight away, on any machine, with no review`
                      : 'Dry-run first: shows the index that would be published'
                }
                onClick={() => void (audience === 'org' ? shipToOrg() : startPublish())}
              >
                <ArrowUp size={15} />
                {pub.step === 'previewing'
                  ? 'Previewing…'
                  : audience === 'org'
                    ? `Ship to ${orgName}`
                    : `Publish${agent.version ? ` v${agent.version}` : ''}`}
              </button>
            ) : null}
            <button
              className="ghost-btn"
              disabled={packing}
              title="package_agent — build the .agentpkg without publishing"
              onClick={() => void runPackage()}
            >
              <Download size={15} />
              {packing ? 'Packaging…' : 'Download .agentpkg'}
            </button>
          </div>

          {/* An org ship never reaches this block: it has no preview to confirm, and the notice
              below is about a PUBLIC artifact and a registry index — neither of which it touches.
              It goes idle -> publishing -> done, and reports underneath like any other run. */}
          {audience === 'org' && pub.step === 'publishing' && (
            <p className="ship-confirm-note">
              <Shield size={14} />
              Shipping to {orgName} — every member will resolve it read-only.
            </p>
          )}

          {audience === 'marketplace' && (pub.step === 'confirm' || pub.step === 'publishing') && (
            <div className="ship-confirm">
              <p className="ship-confirm-note">
                <Shield size={14} />
                This uploads a PUBLIC artifact and rewrites the registry index every client reads.
                The preview below lists every bundle that will be in the published index.
              </p>
              <pre className="ship-report">{pub.preview}</pre>
              <div className="ship-actions">
                <button
                  className="prime-btn"
                  disabled={pub.step === 'publishing'}
                  onClick={() => void confirmPublish(pub.preview)}
                >
                  {pub.step === 'publishing' ? 'Publishing…' : 'Confirm publish'}
                </button>
                <button
                  className="ghost-btn"
                  disabled={pub.step === 'publishing'}
                  onClick={() => setPub({ step: 'done', text: `cancelled — nothing was uploaded.\n\n${pub.preview}`, bad: false })}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          {pub.step === 'done' && (
            <pre className={`ship-report ${pub.bad ? 'is-bad' : ''}`}>{pub.text}</pre>
          )}
          {packOut && <pre className={`ship-report ${packOut.bad ? 'is-bad' : ''}`}>{packOut.text}</pre>}
        </div>

        <aside className="ship-panel">
          <span className="lp-side-label">The listing</span>
          {/* Composed from the roster row's real fields — how the marketplace card will read.
              Install is decorative: this machine already has the agent. */}
          <div className="ship-listing">
            <div className="ship-listing-head">
              <span className="subj-avatar" style={{ background: agentColor(agent.color, agent.id) }}>
                {agentInitials(agent.name, agent.id)}
              </span>
              <span className="ship-listing-names">
                <span className="ship-listing-name">{agent.name || agent.id}</span>
                <span className="ship-listing-by">
                  {[authorLabel || null, agent.version ? `v${agent.version}` : null].filter(Boolean).join(' · ')}
                </span>
              </span>
            </div>
            <p className="ship-listing-desc">{agent.description || agent.tagline || 'No description yet.'}</p>
            <button className="prime-btn wide" disabled title="How the button reads to a stranger — this machine already has the agent">
              Install
            </button>
          </div>

          <span className="lp-side-label">Bundle</span>
          <p className="lp-side-empty">
            The bundle's contents exist inside a package run — Download builds one and reports what
            went in and what was left out.
          </p>
          <p className="lp-side-empty">
            <Play size={12} /> workspace/, sessions/ and app/ source never ship.
          </p>
        </aside>
      </div>
    </div>
  )
}
