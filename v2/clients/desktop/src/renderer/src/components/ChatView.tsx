import { FormEvent, useEffect, useRef, useState } from 'react'
import { Plus, ArrowUp, Square, Terminal, Check, MessageSquare, Paperclip, Users, X } from 'lucide-react'

import logo from '../assets/nakama.svg'
import { agentColor, agentInitials, agentTag } from '../lib/agentPresentation'
import { dayLabel, sameDay } from '../lib/timefmt'
import { useApp, type OutgoingAttachment } from '../state/store'
import MessageItem from './MessageItem'
import TabBar from './TabBar'

// Fallback starters when the agent has no server-side suggestions (yet)
const DEFAULT_SUGGESTIONS = [
  'Summarize today’s changes in the repo.',
  'Run the full test suite and report failures.',
  'Draft a short release note for the latest changes.'
]
const SUGGESTION_ICONS = [<Terminal size={15} key="t" />, <Check size={15} key="c" />, <MessageSquare size={15} key="m" />]

function fileToAttachment(f: File): Promise<OutgoingAttachment> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve({ name: f.name, mimeType: f.type || 'application/octet-stream', dataBase64: String(r.result).split(',')[1] || '' })
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
}

export default function ChatView() {
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const session = useApp((s) => s.sessions[s.currentSessionKey])
  const currentAgentId = useApp((s) => s.currentAgentId)
  const agents = useApp((s) => s.agents)
  const projectName = useApp((s) => s.projects.find((p) => p.id === s.currentProjectId)?.name)
  const hello = useApp((s) => s.hello)
  const connection = useApp((s) => s.connection)
  const sendMessage = useApp((s) => s.sendMessage)
  const abortRun = useApp((s) => s.abortRun)
  const composerSeed = useApp((s) => s.composerSeed)

  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<OutgoingAttachment[]>([])
  const [menuOpen, setMenuOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const items = session?.items || []
  const running = session?.running || false
  const empty = items.length === 0

  const currentAgent = agents.find((a) => a.id === currentAgentId)
  const agentName = currentAgent?.name || hello?.agentName || currentAgentId || 'agent'
  // status-strip data (Claude-style): the model that ACTUALLY ran the latest step + its token
  // usage, live while running, kept out of the scrollback. Falls back to the configured model.
  const usage = session?.usage
  const model = usage?.model || hello?.model || ''
  const usageStr = usage ? `↑ ${usage.tokensIn.toLocaleString()} · ↓ ${usage.tokensOut.toLocaleString()} tok` : ''
  const suggestions = currentAgent?.suggestions?.length ? currentAgent.suggestions : DEFAULT_SUGGESTIONS

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [items])

  // a user message's Edit action loads its text into the composer for tweaking + re-send
  useEffect(() => {
    if (!composerSeed) return
    setDraft(composerSeed.text)
    const el = taRef.current
    if (el) {
      el.focus()
      // caret to the end
      requestAnimationFrame(() => el.setSelectionRange(el.value.length, el.value.length))
    }
  }, [composerSeed])

  // grow the composer to fit its content — measured from the RENDERED height (scrollHeight),
  // so soft-wrapped long lines grow it too, not only explicit Shift+Enter newlines
  useEffect(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [draft])

  function submit(e?: FormEvent) {
    e?.preventDefault()
    const text = draft.trim()
    if ((!text && pending.length === 0) || running || connection !== 'open') return
    const atts = pending
    setDraft('')
    setPending([])
    void sendMessage(text, atts.length ? atts : undefined)
  }

  async function pickFiles(list: FileList | null) {
    if (!list || list.length === 0) return
    const atts = await Promise.all(Array.from(list).map(fileToAttachment))
    setPending((p) => [...p, ...atts])
  }

  function mentionAgent(name: string) {
    setMenuOpen(false)
    setDraft((d) => `${d}${d && !d.endsWith(' ') ? ' ' : ''}@${name} `)
  }

  // date separators between calendar days
  const rendered: JSX.Element[] = []
  let lastTs: number | undefined
  items.forEach((item, i) => {
    if (item.ts && (!lastTs || !sameDay(item.ts, lastTs))) {
      rendered.push(<div key={`day-${i}`} className="msg-system">{dayLabel(item.ts)}</div>)
    }
    if (item.ts) lastTs = item.ts
    rendered.push(<MessageItem key={i} item={item} />)
  })

  const composer = (
    <form className="composer" onSubmit={submit}>
      {pending.length > 0 && (
        <div className="composer-atts">
          {pending.map((a, i) => (
            <span className="att-chip" key={`${a.name}-${i}`} title={a.name}>
              <Paperclip size={12} />
              <span className="att-name">{a.name}</span>
              <button type="button" className="att-remove" title="remove" onClick={() => setPending((p) => p.filter((_, j) => j !== i))}>
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-box">
        <div className="composer-attach-wrap">
          <button
            type="button"
            className={`composer-attach ${menuOpen ? 'active' : ''}`}
            title="add"
            onClick={() => setMenuOpen((v) => !v)}
          >
            <Plus size={19} />
          </button>
          {menuOpen && (
            <>
              <div className="composer-menu-backdrop" onClick={() => setMenuOpen(false)} />
              <div className="composer-menu" role="menu">
                <button type="button" className="cmenu-item" onClick={() => { setMenuOpen(false); fileRef.current?.click() }}>
                  <Paperclip size={16} />
                  <span className="cmenu-main"><span className="cmenu-title">Add photos &amp; files</span><span className="cmenu-sub">Upload from computer</span></span>
                </button>
                <div className="cmenu-sep" />
                <div className="cmenu-label"><Users size={13} />Message an agent</div>
                {agents.map((a) => (
                  <button type="button" className="cmenu-item cmenu-agent" key={a.id} onClick={() => mentionAgent(a.name || a.id)}>
                    <span className="avatar avatar--sm" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(a.name, a.id)}</span>
                    <span className="cmenu-main"><span className="cmenu-title">{a.name || a.id}</span><span className="cmenu-sub">{a.tagline || agentTag(a.id)}</span></span>
                  </button>
                ))}
              </div>
            </>
          )}
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { void pickFiles(e.target.files); e.target.value = '' }} />
        </div>
        <textarea
          ref={taRef}
          value={draft}
          placeholder={connection === 'open' ? `Message ${agentName}…` : 'connecting…'}
          disabled={connection !== 'open'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
          rows={1}
        />
        {running ? (
          <button type="button" className="composer-send stop" onClick={() => void abortRun()} title="stop the run">
            <Square size={13} fill="currentColor" strokeWidth={0} />
          </button>
        ) : (
          <button type="submit" className={`composer-send ${draft.trim() || pending.length ? 'ready' : ''}`} disabled={(!draft.trim() && !pending.length) || connection !== 'open'} title="send">
            <ArrowUp size={18} />
          </button>
        )}
      </div>
      <div className="composer-hint">
        <span className="hint-model">{model || 'agentd'}</span>
        {usageStr && <><span className="hint-sep"> · </span><span className="hint-toks">{usageStr}</span></>}
        <span className="hint-sep"> · </span>
        <span className="hint-note">{running ? 'running — press Stop to interrupt' : 'runs locally'}</span>
      </div>
    </form>
  )

  const greeting = (
    <div className="empty-state">
      <img className="empty-logo" src={logo} alt="" />
      <div className="empty-title">{agentName}</div>
      <div className="empty-sub">
        {projectName
          ? `New chat in ${projectName} — ask anything.`
          : currentAgentId === 'main' || !currentAgentId
            ? 'Ask anything — tools, files, browsing and your installed agents are all here.'
            : `You’re talking to ${agentName}. ${currentAgent?.tagline || agentTag(currentAgentId)}.`}
      </div>
    </div>
  )

  const suggestionRow = (
    <div className="suggestions">
      {suggestions.slice(0, 3).map((text, i) => (
        <button key={text} className="suggestion" onClick={() => setDraft(text)}>
          {SUGGESTION_ICONS[i % SUGGESTION_ICONS.length]}
          {text}
        </button>
      ))}
    </div>
  )

  return (
    <div className={`chat ${empty ? 'empty' : ''}`}>
      <TabBar />

      {empty ? (
        // ChatGPT/Gemini-style: greeting, then the input centered in the page, suggestions below
        <div className="chat-hero">
          {greeting}
          <div className="chat-hero-composer">{composer}</div>
          {suggestionRow}
        </div>
      ) : (
        <>
          <div className="chat-scroll" ref={scrollRef}>
            <div className="chat-col">{rendered}</div>
          </div>
          {composer}
        </>
      )}
    </div>
  )
}
