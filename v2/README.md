# agentd

Minimal terminal agent: a WebSocket gateway daemon running an LLM ⇄ tool loop,
plus a thin terminal client. Any LLM via LiteLLM. Self-contained: keys come from
`v2/.env`, runtime data goes to `v2/.agentd/`, packages live in `v2/.venv/`.

## Architecture

```
terminal client (python -m clients.terminal)
    │  WebSocket: chat.send {sessionKey, message, idempotencyKey}
    ▼
gateway (python -m agent_runtime)            ← responds {runId} immediately, runs async
    │  agent loop: LLM → tool calls → execute → results → LLM … until done
    │  + continuation/verification retries (planning/reasoning/empty, verify hook)
    ▼
events streamed back (chat.event)     ← text/thinking deltas, tool activity, lifecycle
transcripts persisted as JSONL        ← v2/.agentd/sessions/<sessionKey>.jsonl
```

Tools: `read` `write` `edit` `exec` `process` `web_search` `web_fetch` `browser`
(+ `computer`, opt-in — see below)

Skills: drop-in `SKILL.md` playbooks in [skills/](skills/) — know-how the agent
reads on demand (not callable tools). Add your own; see [skills/README.md](skills/README.md).

Plugins & tools: every tool is a drop-in plugin under [plugins/](plugins/). To create,
configure, enable/disable, or override tools per agent — and to understand the whole
`plugins → tools → model` config model — read **[plugins/README.md](plugins/README.md)**
(the complete source of truth).

### Computer use (`computer` tool — opt-in, OFF by default)

Lets the agent operate the PC's GUI like a human — open and control **any** app
(click, type, scroll, drag, upload). It runs its own screenshot → vision-model →
mouse/keyboard loop using a **dedicated computer-use model**, decoupled from the
main agent model.

```
AGENTD_COMPUTER_ENABLED=1                                  # enable it (off by default)
AGENTD_COMPUTER_MODEL=gemini-2.5-computer-use-preview-10-2025   # default; needs GEMINI_API_KEY
AGENTD_COMPUTER_MAX_STEPS=25       # loop step cap
AGENTD_COMPUTER_CAPTURE=primary    # primary | virtual (multi-monitor, best-effort)
```

- **Kill switch:** slam the mouse into any screen corner to abort instantly
  (pyautogui failsafe). The step cap and `/abort` also stop it.
- **⚠️ Privacy:** every step sends a **full-screen screenshot** to the computer-use
  model — it can capture passwords or any sensitive on-screen content. Keep it off
  unless needed; for sensitive use, point `AGENTD_COMPUTER_MODEL` at a trusted/Vertex
  endpoint. The model is decoupled, so this is independent of your main agent model.
- **Deps:** `pyautogui`, `Pillow`, `google-genai` (in `requirements.txt`); the tool
  silently stays disabled if they're missing.

## Setup (one time)

```powershell
cd v2
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium    # for the browser tool
```

Then create `v2/.env` (copy `.env.example` and fill in a provider key):

```
GEMINI_API_KEY=...            # or ANTHROPIC_API_KEY / OPENAI_API_KEY
AGENTD_MODEL=gemini/gemini-3.1-pro-preview    # any LiteLLM model id (default)
AGENTD_REASONING=medium               # off | low | medium | high (thinking)
BRAVE_API_KEY=...                      # optional; web_search falls back to DuckDuckGo
```

## Run

Use the local venv's python so it stays isolated:

```powershell
# Terminal 1 (gateway)
.\.venv\Scripts\python.exe -X utf8 -m agent_runtime

# Terminal 2 (client)
.\.venv\Scripts\python.exe -X utf8 -m clients.terminal      # --session <id> to resume
```

Client commands: `/abort` `/new` `/quit`

Tip: `.\.venv\Scripts\Activate.ps1` once, then plain `python -X utf8 -m agent_runtime`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```
