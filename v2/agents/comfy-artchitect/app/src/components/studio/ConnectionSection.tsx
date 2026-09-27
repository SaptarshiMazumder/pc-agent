/* Where THIS ACCOUNT's ComfyUI runs — shown in the Workspace's Connection section, and as the
 * question the chat asks before anything is rented (ConnectionPrompt).
 *
 * ONE LINE WHEN IT IS SETTLED, THE CHOOSER WHEN IT IS NOT. Once the account has a machine, the
 * section is a summary card — which machine, whether it answers, where its models are kept — with
 * a Change button. The chooser (a three-way switch) opens only on Change, or while nothing has been
 * chosen yet. Nothing is removed by folding: every key, the machine list and the storage answer
 * are one click away.
 *
 * THREE CHOICES, and none of them is made for the person:
 *   Rent a GPU         we start one and stop it; credits while it runs. The button is the approval,
 *                      kept for the account until they change it here.
 *   My Vast machine    their Vast API key, then a machine picked from their account — works
 *                      exactly like the rented one, installs included; they pay Vast, not us.
 *   My ComfyUI link    any ComfyUI address — models install only through ComfyUI-Manager or by
 *                      hand, which the option says before they pick it.
 * Nothing changes until Rent, Use this or Connect answers.
 */

import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { ComfyConnection, ConnectionKind, Storage } from './useComfyConnection'
import { OWN_KEYS, useOwnComfyKeys, VAST_KEY } from './useOwnComfyKeys'
import { useVastMachines, type VastMachine } from './useVastMachines'

const OPTIONS: { kind: ConnectionKind; title: string; note: string }[] = [
  { kind: 'rented', title: 'Rent a GPU', note: 'We start one when a job needs it and stop it when idle. Uses credits while it runs.' },
  {
    kind: 'user_vast',
    title: 'My Vast machine',
    note: 'Your Vast API key, then pick a machine (ComfyUI template). Works exactly like ours — you pay Vast.',
  },
  {
    kind: 'user_url',
    title: 'My ComfyUI link',
    note: 'Any ComfyUI address. Models install only through ComfyUI-Manager, or you add the files yourself.',
  },
]

export const connectionTitle = (kind: ConnectionKind | null): string =>
  kind === 'user_vast'
    ? 'Your Vast machine'
    : kind === 'user_url'
      ? 'Your ComfyUI'
      : kind === 'rented'
        ? 'Rented GPU'
        : 'Not chosen'

export function ConnectionSection({ client, connection }: { client?: AgentdClient; connection: ComfyConnection }) {
  const [changing, setChanging] = useState(false)
  // A choice that landed closes the chooser: the summary says what is in use now.
  useEffect(() => setChanging(false), [connection.kind, connection.label, connection.vastMachineId])

  const own = connection.kind === 'user_vast' || connection.kind === 'user_url'
  const choosing = !connection.kind || changing
  return (
    <div className="cn">
      {!choosing && <Summary client={client} connection={connection} onChange={() => setChanging(true)} />}
      {choosing && (
        <Chooser client={client} connection={connection} onDone={connection.kind ? () => setChanging(false) : undefined} />
      )}
      {connection.error && <p className="cn-err">{connection.error}</p>}
      {own && <OwnKeys client={client} />}
    </div>
  )
}

/* THE SETTLED STATE: what runs the jobs, in one card. */
function Summary({ client, connection, onChange }: { client?: AgentdClient; connection: ComfyConnection; onChange: () => void }) {
  const vast = connection.kind === 'user_vast'
  const { machines } = useVastMachines(client, vast)
  const machine = vast ? (machines || []).find((m) => m.id === connection.vastMachineId) : undefined
  // THE MACHINE IN USE IS GONE from the Vast account (destroyed): say so, and send them to the list.
  const gone = vast && !!machines && connection.vastMachineId != null && !machine
  if (gone) {
    return (
      <div className="cn-sum is-gone">
        <div className="cn-sum-main">
          <span className="cn-dot is-off" aria-hidden="true" />
          <span className="cn-sum-text">
            <b>{connection.label} is gone</b>
            <span className="cn-note">It is no longer on your Vast account — pick a running machine.</span>
          </span>
          <button className="refs-slot-btn" onClick={onChange}>
            Pick a machine
          </button>
        </div>
      </div>
    )
  }

  const title = machine
    ? machine.gpu
    : connection.kind === 'rented'
      ? 'Rented GPU'
      : connection.label || connectionTitle(connection.kind)
  const sub = machine
    ? `Vast #${machine.id}`
    : vast
      ? connection.label
      : connection.kind === 'rented'
        ? 'Started when a job needs it, stopped when idle — credits while it runs'
        : 'Your ComfyUI — models install through ComfyUI-Manager, or you add them yourself'
  return (
    <div className="cn-sum">
      <div className="cn-sum-main">
        {machine && <span className={`cn-dot ${dotFor(machine.status)}`} aria-hidden="true" />}
        <span className="cn-sum-text">
          <b>{title}</b>
          <span className="cn-note">
            {sub}
            {machine && ` · ${statusWord(machine.status)}`}
          </span>
        </span>
        <button className="cn-link-btn" onClick={onChange}>
          Change
        </button>
      </div>
      {vast && connection.storage && <p className="cn-note cn-sum-storage">{storageLine(connection.storage)}</p>}
    </div>
  )
}

/* THE CHOOSER: one switch, then only the picked option's controls. */
function Chooser({ client, connection, onDone }: { client?: AgentdClient; connection: ComfyConnection; onDone?: () => void }) {
  const [picked, setPicked] = useState<ConnectionKind | null>(connection.kind)
  const option = OPTIONS.find((o) => o.kind === picked)
  return (
    <div className="cn-choose">
      <div className="cn-seg" role="tablist" aria-label="Where ComfyUI runs">
        {OPTIONS.map((o) => (
          <button
            key={o.kind}
            role="tab"
            aria-selected={picked === o.kind}
            className={`cn-seg-btn${picked === o.kind ? ' is-on' : ''}`}
            disabled={connection.busy}
            onClick={() => setPicked(o.kind)}
          >
            {o.title}
            {connection.kind === o.kind && <span className="cn-seg-dot" aria-label="in use" />}
          </button>
        ))}
      </div>
      {option && <p className="cn-note">{option.note}</p>}

      {picked === 'rented' &&
        (connection.kind === 'rented' ? (
          <p className="cn-note">This is what you use now.</p>
        ) : (
          <div className="cn-row">
            <button className="refs-slot-btn" disabled={connection.busy} onClick={() => void connection.rent()}>
              {connection.busy ? 'Saving…' : 'Rent a GPU'}
            </button>
            <span className="cn-note">Every chat on this account uses it.</span>
          </div>
        ))}
      {picked === 'user_vast' && <VastPicker client={client} connection={connection} />}
      {picked === 'user_url' && <LinkField connection={connection} />}

      {onDone && (
        <button className="cn-link-btn cn-done" onClick={onDone}>
          Done
        </button>
      )}
    </div>
  )
}

/* ANY COMFYUI ADDRESS — the field always shows what is saved. */
function LinkField({ connection }: { connection: ComfyConnection }) {
  const [url, setUrl] = useState(connection.kind === 'user_url' ? connection.link : '')
  useEffect(() => setUrl(connection.kind === 'user_url' ? connection.link : ''), [connection.kind, connection.link])
  const saved = connection.kind === 'user_url' && url.trim() === connection.link
  return (
    <div className="cn-link">
      <div className="cn-row">
        <input
          className="cn-input st-mono"
          value={url}
          placeholder="http://host:8188"
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && url.trim() && !saved && void connection.connect(url.trim())}
        />
        <button
          className="refs-slot-btn"
          disabled={!url.trim() || connection.busy || saved}
          onClick={() => void connection.connect(url.trim())}
        >
          {connection.busy ? 'Checking…' : connection.kind === 'user_url' ? 'Save' : 'Connect'}
        </button>
      </div>
      <p className="cn-note">
        On the web, the address has to be reachable from the internet — a ComfyUI on your own computer
        works from the desktop app.
      </p>
    </div>
  )
}

/* MY VAST MACHINE: the key once, then a pick from the account's machines — no links, no tokens. */
function VastPicker({ client, connection }: { client?: AgentdClient; connection: ComfyConnection }) {
  const { keys, message, save } = useOwnComfyKeys(client, VAST_KEY)
  const [draft, setDraft] = useState('')
  const [replacing, setReplacing] = useState(false)
  const keySet = keys.some((k) => k.isSet)
  const { machines, loading, error, refresh } = useVastMachines(client, keySet)
  const inUse = connection.kind === 'user_vast' ? connection.vastMachineId : null

  const saveKey = () =>
    void save({ USER_VAST_API_KEY: draft.trim() }).then(() => {
      setDraft('')
      setReplacing(false)
    })

  return (
    <div className="cn-link">
      {keySet && !replacing ? (
        <div className="cn-row">
          <span className="cn-note">
            Vast API key <span className="cn-set">· saved</span>
          </span>
          <button className="cn-link-btn" onClick={() => setReplacing(true)}>
            Replace
          </button>
        </div>
      ) : (
        <div className="cn-row">
          <input
            type="password"
            className="cn-input"
            value={draft}
            placeholder="Vast API key — Vast → Account → Keys"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && draft.trim() && saveKey()}
          />
          <button className="refs-slot-btn" disabled={!draft.trim()} onClick={saveKey}>
            Save
          </button>
          {replacing && (
            <button className="cn-link-btn" onClick={() => setReplacing(false)}>
              Cancel
            </button>
          )}
        </div>
      )}
      {message && message !== 'Saved.' && <p className="cn-err">{message}</p>}

      {keySet && (
        <div className="cn-machines">
          {error && <p className="cn-err">{error}</p>}
          {machines && !machines.length && (
            <p className="cn-note">No machines on your Vast account — rent one with the ComfyUI template, then refresh.</p>
          )}
          {(machines || []).map((m) => (
            <MachineRow
              key={m.id}
              machine={m}
              inUse={inUse === m.id}
              busy={connection.busy}
              storage={inUse === m.id ? connection.storage : null}
              onUse={() => void connection.useVastMachine(m.id)}
            />
          ))}
          <button className="cn-link-btn" disabled={loading} onClick={() => void refresh()}>
            {loading ? 'Refreshing…' : '↻ Refresh list'}
          </button>
        </div>
      )}
    </div>
  )
}

function MachineRow({
  machine,
  inUse,
  busy,
  storage,
  onUse,
}: {
  machine: VastMachine
  inUse: boolean
  busy: boolean
  storage: Storage | null
  onUse: () => void
}) {
  return (
    <div className={`cn-machine${inUse ? ' is-on' : ''}${machine.running ? '' : ' is-off'}`}>
      <div className="cn-machine-main">
        <span className={`cn-dot ${dotFor(machine.status)}`} aria-hidden="true" />
        <span className="cn-sum-text">
          <b>{machine.gpu}</b>
          <span className="cn-note">
            #{machine.id} · {machine.running ? statusWord(machine.status) : `${statusWord(machine.status)} — start it on Vast`}
          </span>
        </span>
        {inUse ? (
          <span className="cn-set">in use</span>
        ) : (
          <button className="refs-slot-btn" disabled={!machine.running || busy} onClick={onUse}>
            {busy ? 'Connecting…' : 'Use this'}
          </button>
        )}
      </div>
      {storage && <p className="cn-note cn-sum-storage">{storageLine(storage)}</p>}
    </div>
  )
}

/* Vast's own status words, said the way a person reads them. */
function statusWord(status: string): string {
  if (status === 'running') return 'running'
  if (['loading', 'created', 'starting', 'scheduling'].includes(status)) return 'starting'
  if (['exited', 'stopped', 'offline'].includes(status)) return 'stopped'
  return status || 'unknown'
}

function dotFor(status: string): string {
  const word = statusWord(status)
  return word === 'running' ? 'is-ok' : word === 'starting' ? 'is-warn' : 'is-off'
}

/* WHERE A VAST MACHINE KEEPS MODELS — the one thing that decides whether a download is paid for
   once or every time the machine is replaced. */
function storageLine(s: Storage): string {
  if (s.kind === 'volume') return `Models are saved to your volume at ${s.path} — kept when the machine goes.`
  if (s.kind === 'workspace_volume') return 'Models are saved to your /workspace volume — kept when the machine goes.'
  if (s.kind === 'disk') return 'Models are saved on the machine’s own disk — lost if it is destroyed.'
  if (s.kind === 'unchecked') return `Storage could not be checked (${s.error}); it is checked again before the first download.`
  return 'Checking where models will be saved…'
}

/* THEIR KEYS, for their machine only — optional, write-only, folded until wanted. */
function OwnKeys({ client }: { client?: AgentdClient }) {
  const { keys, message, save } = useOwnComfyKeys(client, OWN_KEYS)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const changed = Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim()))
  if (!keys.length) return message ? <p className="cn-err">{message}</p> : null
  const saved = keys.filter((k) => k.isSet).length
  return (
    <div className="cn-keys">
      <button className="cn-fold" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span> Keys for paid &amp; gated models (optional)
        <span className="cn-note"> · {saved} of {keys.length} saved</span>
      </button>
      {open && (
        <>
          <p className="cn-note">Used only on your machine. Paid models run on your Comfy account.</p>
          {keys.map((k) => (
            <label key={k.key} className="cn-key">
              <span>
                {k.label}
                {k.isSet && <span className="cn-set"> · saved</span>}
              </span>
              <input
                type="password"
                className="cn-input"
                value={draft[k.key] || ''}
                placeholder={k.isSet ? '•••••••• saved — type to replace' : 'not set'}
                title={k.help}
                onChange={(e) => setDraft((d) => ({ ...d, [k.key]: e.target.value }))}
              />
            </label>
          ))}
          <div className="cn-row">
            <button
              className="refs-slot-btn"
              disabled={!Object.keys(changed).length}
              onClick={() => void save(changed).then(() => setDraft({}))}
            >
              Save keys
            </button>
            {message && <span className="cn-note">{message}</span>}
          </div>
        </>
      )}
    </div>
  )
}
