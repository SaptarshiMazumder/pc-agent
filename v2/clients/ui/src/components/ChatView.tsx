import { FormEvent, useEffect, useRef, useState, type DragEvent as ReactDragEvent, type ClipboardEvent as ReactClipboardEvent } from 'react'
import { Plus, ArrowUp, Square, Terminal, Check, MessageSquare, Paperclip, Users, X, Upload } from 'lucide-react'

import logo from '../assets/nakama.svg'
import { agentColor, agentInitials, agentLabel, agentTag, MAIN_AGENT_ID } from '../lib/agentPresentation'
import { dayLabel, sameDay } from '../lib/timefmt'
import { useApp, type OutgoingAttachment } from '../state/store'
import FileName from './FileName'
import MessageItem from './MessageItem'
import TabBar from './TabBar'

// Fallback starters when the agent has no server-side suggestions (yet)
const DEFAULT_SUGGESTIONS = [
  'Summarize today’s changes in the repo.',
  'Run the full test suite and report failures.',
  'Draft a short release note for the latest changes.'
]
const SUGGESTION_ICONS = [<Terminal size={15} key="t" />, <Check size={15} key="c" />, <MessageSquare size={15} key="m" />]

// how many attachments may ride a single message — enforced across picker, drop, and paste
const MAX_ATTACHMENTS = 10

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
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  // dragenter/leave fire for every nested child; count depth so the overlay only clears
  // when the cursor truly leaves the chat, not when it crosses an inner element.
  const dragDepth = useRef(0)
  const items = session?.items || []
  const running = session?.running || false
  const empty = items.length === 0

  const currentAgent = agents.find((a) => a.id === currentAgentId)
  const agentName = agentLabel(currentAgent?.name, currentAgentId, hello?.agentName)
  // inside a project the PROJECT is the identity you're addressing (the answering
  // agent is plumbing, shown only in the project's "answers as" picker)
  const chatName = projectName || agentName
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
    const room = MAX_ATTACHMENTS - pending.length
    if (room <= 0) return // already at the cap — the "Max N files" hint explains why
    const atts = await Promise.all(Array.from(list).slice(0, room).map(fileToAttachment))
    setPending((p) => [...p, ...atts].slice(0, MAX_ATTACHMENTS)) // hard guard against races
  }

  // --- drag & drop (drop files anywhere on the chat, ChatGPT/Gemini-style) --------------
  function hasFiles(dt: DataTransfer | null | undefined): boolean {
    // 'Files' in types means an OS file drag — ignore text/element drags (canvas, selection)
    return !!dt && Array.from(dt.types).includes('Files')
  }
  function onDragEnter(e: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(e.dataTransfer)) return
    e.preventDefault()
    dragDepth.current += 1
    setDragging(true)
  }
  function onDragOver(e: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(e.dataTransfer)) return
    e.preventDefault() // required for the drop to be accepted
    e.dataTransfer.dropEffect = 'copy'
  }
  function onDragLeave(e: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(e.dataTransfer)) return
    dragDepth.current -= 1
    if (dragDepth.current <= 0) {
      dragDepth.current = 0
      setDragging(false)
    }
  }
  function onDrop(e: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(e.dataTransfer)) return
    e.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    void pickFiles(e.dataTransfer.files)
  }

  // paste an image/file straight from the clipboard (Ctrl/Cmd+V) — text pastes unaffected
  function onPaste(e: ReactClipboardEvent<HTMLTextAreaElement>) {
    if (e.clipboardData?.files?.length) {
      e.preventDefault()
      void pickFiles(e.clipboardData.files)
    }
  }

  // Safety net: without this, dropping a file that MISSES the chat area makes Electron
  // navigate the window to the file:// URL (whole UI replaced by the image). Swallow any
  // file drop at the window level; the chat's own onDrop still handles real attaches.
  useEffect(() => {
    const swallow = (ev: globalThis.DragEvent) => {
      if (hasFiles(ev.dataTransfer)) ev.preventDefault()
    }
    window.addEventListener('dragover', swallow)
    window.addEventListener('drop', swallow)
    return () => {
      window.removeEventListener('dragover', swallow)
      window.removeEventListener('drop', swallow)
    }
  }, [])

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
            <span className={`att-chip ${a.mimeType.startsWith('image/') ? 'att-chip--img' : ''}`} key={`${a.name}-${i}`} title={a.name}>
              {a.mimeType.startsWith('image/') ? (
                <img className="att-thumb" src={`data:${a.mimeType};base64,${a.dataBase64}`} alt="" />
              ) : (
                <Paperclip size={12} />
              )}
              <FileName name={a.name} className="att-name" />
              <button type="button" className="att-remove" title="remove" onClick={() => setPending((p) => p.filter((_, j) => j !== i))}>
                <X size={12} />
              </button>
            </span>
          ))}
          {pending.length >= MAX_ATTACHMENTS && <span className="att-limit">Max {MAX_ATTACHMENTS} files</span>}
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
                {agents.map((a) => {
                  // one label for display AND the inserted @mention — the gateway resolves
                  // mentions by id OR display name, so what the user sees is what routes.
                  const label = agentLabel(a.name, a.id, hello?.agentName)
                  return (
                    <button type="button" className="cmenu-item cmenu-agent" key={a.id} onClick={() => mentionAgent(label)}>
                      <span className="avatar avatar--sm" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(label, a.id)}</span>
                      <span className="cmenu-main"><span className="cmenu-title">{label}</span><span className="cmenu-sub">{a.tagline || agentTag(a.id)}</span></span>
                    </button>
                  )
                })}
              </div>
            </>
          )}
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { void pickFiles(e.target.files); e.target.value = '' }} />
        </div>
        <textarea
          ref={taRef}
          value={draft}
          placeholder={connection === 'open' ? `Message ${chatName}…` : 'connecting…'}
          disabled={connection !== 'open'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
          onPaste={onPaste}
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
      <div className="empty-title">{chatName}</div>
      <div className="empty-sub">
        {projectName
          ? 'New chat in this project — ask anything.'
          : currentAgentId === MAIN_AGENT_ID || !currentAgentId
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
    <div
      className={`chat ${empty ? 'empty' : ''}`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <TabBar />
      {dragging && (
        <div className="chat-dropzone" aria-hidden>
          <div className="chat-dropzone-inner">
            <Upload size={28} />
            <span>Drop files to attach</span>
          </div>
        </div>
      )}

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
